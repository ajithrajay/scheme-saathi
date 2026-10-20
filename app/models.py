from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Each value is explicitly supplied by the citizen. Protected identifiers are
# excluded from LLM intake and are only available to the local browser runner.
FIELDS = {
    "relation_name": {"en": "Father / husband name (kit form)", "ta": "தந்தை / கணவர் பெயர்", "type": "text", "private": True},
    "area_type": {"en": "Area type (kit form)", "ta": "பகுதி வகை", "type": "select", "options": ["urban", "rural"]},
    "block": {"en": "Block / horticulture centre (exact portal name)", "ta": "வட்டாரம்", "type": "text", "private": True},
    "village": {"en": "Village (rural kit applications)", "ta": "கிராமம்", "type": "text", "private": True},
    "kit_category": {"en": "Kit portal category (choose the official option)", "ta": "தோட்டக்கலை படிவ வகுப்பு", "type": "select", "options": ["SC", "ST", "General"], "private": True},
    "full_name": {"en": "Full name", "ta": "முழுப் பெயர்", "type": "text"},
    "state": {"en": "State of residence", "ta": "வசிக்கும் மாநிலம்", "type": "text"},
    "district": {"en": "District", "ta": "மாவட்டம்", "type": "text"},
    "gender": {"en": "Gender", "ta": "பாலினம்", "type": "select", "options": ["female", "male", "other"]},
    "date_of_birth": {"en": "Date of birth", "ta": "பிறந்த தேதி", "type": "date"},
    "age": {"en": "Age in completed years", "ta": "வயது", "type": "number", "min": 0, "max": 120},
    "annual_household_income_inr": {"en": "Annual family income (₹)", "ta": "ஆண்டு குடும்ப வருமானம் (₹)", "type": "number", "min": 0},
    "income_financial_year": {"en": "Income financial year (e.g. 2025-26)", "ta": "வருமான நிதியாண்டு", "type": "text"},
    "category": {"en": "Community category", "ta": "சமூகப் பிரிவு", "type": "select", "options": ["General", "BC", "MBC", "OBC", "SC", "ST", "DNC"]},
    "school_type": {"en": "School type", "ta": "பள்ளி வகை", "type": "select", "options": ["government", "government_aided", "local_body", "private", "KVS", "JNV", "residential"]},
    "school_medium": {"en": "School medium", "ta": "பயிற்று மொழி", "type": "select", "options": ["Tamil", "English", "other"]},
    "studied_6_to_12_continuously": {"en": "Classes 6–12 continuously in the stated school type?", "ta": "6 முதல் 12 வரை குறிப்பிட்ட பள்ளி வகையில் தொடர்ந்து படித்தீர்களா?", "type": "boolean"},
    "higher_education_enrolled": {"en": "Currently enrolled in higher education?", "ta": "தற்போது உயர்கல்வியில் சேர்ந்துள்ளீர்களா?", "type": "boolean"},
    "current_class": {"en": "Current school class", "ta": "தற்போதைய வகுப்பு", "type": "number", "min": 1, "max": 12},
    "class_7_percentage": {"en": "Class 7 marks (%)", "ta": "7ஆம் வகுப்பு மதிப்பெண் (%)", "type": "number", "min": 0, "max": 100},
    "selection_test_qualified": {"en": "Qualified the state NMMS selection test?", "ta": "மாநில NMMS தேர்வில் தேர்ச்சி பெற்றீர்களா?", "type": "boolean"},
    "phone": {"en": "Mobile number", "ta": "கைபேசி எண்", "type": "tel", "private": True},
    "email": {"en": "Email", "ta": "மின்னஞ்சல்", "type": "email", "private": True},
    "address": {"en": "Address", "ta": "முகவரி", "type": "text", "private": True},
    "pincode": {"en": "PIN code", "ta": "அஞ்சல் குறியீடு", "type": "text", "private": True},
    "aadhaar_number": {"en": "Aadhaar (only if required for application)", "ta": "ஆதார் எண் (விண்ணப்பத்திற்கு தேவைப்பட்டால்)", "type": "text", "private": True},
    "bank_account": {"en": "Bank account number", "ta": "வங்கி கணக்கு எண்", "type": "text", "private": True},
    "ifsc": {"en": "IFSC", "ta": "IFSC", "type": "text", "private": True},
    "institution_name": {"en": "School / college name", "ta": "பள்ளி / கல்லூரி பெயர்", "type": "text"},
    "umis_id": {"en": "UMIS ID", "ta": "UMIS எண்", "type": "text", "private": True},
    "otr_id": {"en": "NSP OTR ID", "ta": "NSP OTR எண்", "type": "text", "private": True},
}

from .tn_portal_data import EXTRA_FIELDS
FIELDS.update(EXTRA_FIELDS)

def clean_facts(values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in values.items():
        if key not in FIELDS:
            raise ValueError(f"Unknown profile field: {key}")
        if value is None or value == "":
            continue
        meta = FIELDS[key]
        kind = meta["type"]
        if kind == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false")
        elif kind == "number":
            if isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            value = float(value)
            if not meta.get("min", 0) <= value <= meta.get("max", 1_000_000_000):
                raise ValueError(f"{key} is out of range")
            if key in {"age", "current_class"} and not value.is_integer():
                raise ValueError(f"{key} must be a whole number")
        else:
            value = str(value).strip()
            if len(value) > 500:
                raise ValueError(f"{key} is too long")
            if kind == "select" and value not in meta["options"]:
                raise ValueError(f"Invalid {key}")
            if kind == "date" and date.fromisoformat(value) > date.today():
                raise ValueError("Birth date cannot be in the future")
        result[key] = value
    return result

class ProfileUpdate(BaseModel):
    facts: dict[str, Any]
    @field_validator("facts")
    @classmethod
    def check(cls, value):
        return clean_facts(value)

class Intake(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    language: Literal["en", "ta"] = "en"

class SettingsUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=256)
    model: str = Field(default="openai/gpt-oss-20b", max_length=120)

class AssessRequest(BaseModel):
    demo: bool = False
    language: Literal["en", "ta"] = "en"
    auto_apply: bool = False
    auto_submit: bool = False

class ApplicationRequest(BaseModel):
    scheme_id: str
    demo: bool = False
    auto_submit: bool = False

class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    label_ta: str = ""
    op: Literal["ALL", "ANY", "NOT", "eq", "in", "lte", "gte", "lt", "gt", "between"]
    field: str | None = None
    value: Any = None
    children: list["Rule"] = Field(default_factory=list)
    source_id: str | None = None
    source_section: str = ""

class ReviewRequest(BaseModel):
    rule: Rule
    coverage_complete: bool
    review_note: str = Field(min_length=20, max_length=4000)
    window_status: Literal["UNKNOWN", "OPEN", "CLOSED"] = "UNKNOWN"
    window_start: str | None = None
    window_end: str | None = None
