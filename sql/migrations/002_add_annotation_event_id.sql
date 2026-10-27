ALTER TABLE app.annotations
    ADD COLUMN client_event_id uuid;

ALTER TABLE app.annotations
    ADD CONSTRAINT annotations_client_event_unique UNIQUE (user_id, client_event_id);
