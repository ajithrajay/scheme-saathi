import asyncio
import re
from typing import TypedDict, Any
from langgraph.graph import StateGraph, START, END
from .llm import ModelUnavailable, redact_text
from .models import FIELDS, clean_facts
from .rules import assess, application_readiness
from .sources import retrieve, graph
from .store import now

class Workflow(TypedDict, total=False):
    job_id: str
    language: str
    demo: bool
    profile: dict
    documents: list
    schemes: list
    evidence: list
    results: list
    plan: dict
    verification: dict
    done: list

class Agents:
    def __init__(self, store, llm):
        self.store, self.llm = store, llm
        g = StateGraph(Workflow)
        for name in ["discovery", "eligibility", "planner", "verifier"]:
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "discovery")
        g.add_edge("discovery", "eligibility")
        g.add_edge("eligibility", "planner")
        g.add_edge("planner", "verifier")
        g.add_edge("verifier", END)
        self.graph = g.compile()

    async def profile(self, text, language):
        if not self.llm.available():
            return {"facts": {}, "questions": [], "mode": "form", "message": "Add a Groq key for free-text intake, or fill the profile form. / Groq சாவியைச் சேர்க்கவும் அல்லது சுயவிவரப் படிவத்தை நிரப்பவும்."}
        allowed = {k: v for k, v in FIELDS.items() if not v.get("private")}
        data = await self.llm.json("You are the Profile Agent. Extract only explicitly stated facts into {facts: object, questions: string[]}. Use exactly the supplied field keys and enum values. Never infer caste, income, gender, schooling or eligibility. Annual household income must be explicitly household/family and annual; if only monthly income is stated ask whether it is household income before converting. Preserve numbers exactly. Leave uncertain fields absent. Questions must be in the requested language. Do not include private identifiers.", {"text": redact_text(text), "language": language, "fields": allowed})
        proposed = {k: v for k, v in data.get("facts", {}).items() if k in allowed}
        # Validate each field independently; invalid suggestions never overwrite facts.
        valid, rejected = {}, []
        for key, value in proposed.items():
            try:
                valid.update(clean_facts({key: value}))
            except (ValueError, TypeError):
                rejected.append(key)
        return {"facts": valid, "questions": [q for q in data.get("questions", []) if isinstance(q, str)][:5], "rejected_fields": rejected, "mode": "groq", "message": "Review and confirm the suggested facts before checking eligibility."}

    def checkpoint(self, state, stage, updates):
        updated = {**state, **updates, "done": [*state.get("done", []), stage]}
        self.store.put("checkpoint", state["job_id"], updated)
        self.store.event(state["job_id"], stage, {"discovery": "Official evidence and all catalogue candidates retrieved.", "eligibility": "Versioned conditions evaluated by the rule engine.", "planner": "Document and application steps prepared.", "verifier": "Source references and policy versions verified."}[stage])
        return updated

    async def discovery(self, state):
        if "discovery" in state.get("done", []):
            return state
        schemes = [s for s in self.store.all("scheme") if bool(s.get("is_demo")) == bool(state["demo"])]
        evidence = await asyncio.to_thread(retrieve, self.store, "eligibility schooling income application documents", [s["id"] for s in schemes], 12)
        return self.checkpoint(state, "discovery", {"schemes": schemes, "evidence": evidence})

    async def eligibility(self, state):
        if "eligibility" in state.get("done", []):
            return state
        results = [assess(s, state["profile"], state["documents"]) for s in state["schemes"]]
        return self.checkpoint(state, "eligibility", {"results": results})

    async def planner(self, state):
        if "planner" in state.get("done", []):
            return state
        ta = state["language"] == "ta"
        plan = {"steps": ["சுயவிவர விவரங்களை உறுதிப்படுத்தவும்.", "அதிகாரப்பூர்வ விதிகள் மற்றும் விண்ணப்ப தேதிகளை சரிபார்க்கவும்.", "தேவையான ஆவணங்களைச் சேர்க்கவும்.", "விண்ணப்ப உலாவியைத் திறந்து தொடரவும்."] if ta else ["Confirm your profile details.", "Review official policy coverage and the application window.", "Add the required documents.", "Start the application browser and complete portal verification."], "mode": "deterministic", "graph": graph(self.store, [s["id"] for s in state["schemes"]])}
        if self.llm.available() and not state["demo"]:
            try:
                advice = await self.llm.json("You are the Application Planner Agent. Explain the supplied screening and missing-document results in the requested language. Return {steps: string[], source_ids: string[]}. Use at most six short steps, and only source IDs present in the evidence. Never change screening statuses, promise a benefit, invent a document requirement, or invent URLs. Explicitly mention unresolved rule review and unknown windows when present.", {"language": state["language"], "results": state["results"], "evidence": state["evidence"]}, state["job_id"])
                known = {e["source_id"] for e in state["evidence"]}
                if isinstance(advice.get("steps"), list) and all(isinstance(x, str) for x in advice["steps"]) and set(advice.get("source_ids", [])) <= known:
                    plan.update(steps=advice["steps"][:6], source_ids=advice.get("source_ids", []), mode="groq_draft")
            except ModelUnavailable as exc:
                plan["model_notice"] = str(exc)
        return self.checkpoint(state, "planner", {"plan": plan})

    async def verifier(self, state):
        if "verifier" in state.get("done", []):
            return state
        issues = []
        for result in state["results"]:
            scheme = self.store.get("scheme", result["scheme_id"])
            if scheme["rule_version"] != result["rule_version"] or scheme.get("policy_changed"):
                result["status"] = "REVIEW_REQUIRED"
                result["review_reasons"].append("Policy changed during assessment; run a new assessment after review.")
            for sid in result["source_ids"]:
                if not self.store.get("source", sid):
                    issues.append("Missing source " + sid)
                    result["status"] = "REVIEW_REQUIRED"
            result.update(application_readiness(result, state["profile"]))
        plan = state["plan"]
        if plan["mode"] == "groq_draft":
            try:
                audit = await self.llm.json("You are the Evidence Verifier Agent. Audit this proposed application advice against ONLY supplied evidence and rule results. Return {supported: boolean, issues: string[]}. Flag invented requirements, unverified application windows, promises of approval, or advice contradicting REVIEW_REQUIRED. Do not change rules.", {"plan": plan["steps"], "evidence": state["evidence"], "results": state["results"]}, state["job_id"])
                if audit.get("supported") is True:
                    plan["mode"] = "groq_reviewed_advice"
                else:
                    plan["steps"] = []
                    plan["mode"] = "withheld"
                    issues.extend([str(i) for i in audit.get("issues", [])][:5])
            except ModelUnavailable:
                plan["steps"] = []
                plan["mode"] = "withheld"
                issues.append("AI advice withheld because evidence verification was unavailable. Use the rule results and document checklist.")
        return self.checkpoint(state, "verifier", {"verification": {"issues": issues, "checked_at": now()}, "plan": plan})

    async def run(self, job_id, language="en", demo=False, resume=False):
        job = self.store.get("assessment", job_id)
        try:
            job["status"] = "RUNNING"
            self.store.put("assessment", job_id, job)
            state = self.store.get("checkpoint", job_id) if resume else None
            if not state:
                state = {"job_id": job_id, "language": language, "demo": demo, "profile": job["profile_snapshot"], "documents": job["document_snapshot"], "done": []}
            result = await asyncio.wait_for(self.graph.ainvoke(state), timeout=150)
            job.update(status="COMPLETED", results=result["results"], evidence=result["evidence"], plan=result["plan"], verification=result["verification"], completed_at=now())
        except Exception as exc:
            job.update(status="INTERRUPTED", error="Assessment paused (" + type(exc).__name__ + "). Resume to retry unfinished steps.")
        self.store.put("assessment", job_id, job)
