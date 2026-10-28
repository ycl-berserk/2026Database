CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE app.messages
    ADD COLUMN embedding       vector(1536),
    ADD COLUMN embedding_model text,
    ADD COLUMN embedded_at     timestamptz;

CREATE INDEX messages_embedding_hnsw
    ON app.messages USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
