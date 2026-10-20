from functools import lru_cache

@lru_cache(maxsize=1)
def models():
    from sentence_transformers import SentenceTransformer, CrossEncoder
    return SentenceTransformer("intfloat/multilingual-e5-small"), CrossEncoder("BAAI/bge-reranker-v2-m3")

def rerank(query, passages):
    encoder, cross = models()
    q = encoder.encode(["query: " + query], normalize_embeddings=True)[0]
    vectors = encoder.encode(["passage: " + p["text"] for p in passages], normalize_embeddings=True)
    candidates = sorted(zip(passages, vectors @ q), key=lambda x: x[1], reverse=True)[:20]
    scores = cross.predict([(query, p["text"]) for p, _ in candidates])
    return [{**p, "score": float(score), "retrieval": "multilingual-e5 + bge-reranker"} for (p, _), score in zip(candidates, scores)]
