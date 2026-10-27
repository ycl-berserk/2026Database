CREATE TABLE app.api_tokens (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           bigint       NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    name              varchar(60)  NOT NULL,
    token_prefix      varchar(12)  NOT NULL,
    token_hash        bytea        NOT NULL,
    window_started_at timestamptz,
    window_count      integer      NOT NULL DEFAULT 0,
    last_used_at      timestamptz,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    revoked_at        timestamptz,
    CONSTRAINT api_tokens_hash_unique UNIQUE (token_hash),
    CONSTRAINT api_tokens_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    CONSTRAINT api_tokens_window_check CHECK (window_count >= 0)
);

CREATE INDEX api_tokens_user_idx ON app.api_tokens (user_id, created_at DESC);
