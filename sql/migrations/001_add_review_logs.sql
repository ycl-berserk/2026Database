-- K1：复习事件流水。review_items 只保留最新聚合状态，这里记录每一次动作，
-- 供热力图、留存统计与离线 outbox 幂等重放使用。
CREATE TABLE app.review_logs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    review_item_id  bigint      REFERENCES app.review_items(id) ON DELETE SET NULL,
    action          varchar(10) NOT NULL,
    event_id        uuid,
    interval_days   integer     NOT NULL,
    due_date        date        NOT NULL,
    review_count    integer     NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT review_logs_action_check CHECK (action IN ('review', 'mastered', 'reset')),
    CONSTRAINT review_logs_interval_check CHECK (interval_days > 0),
    CONSTRAINT review_logs_count_check CHECK (review_count >= 0),
    -- 同一用户同一客户端事件只落一次；普通页面操作 event_id 为空，不受影响。
    CONSTRAINT review_logs_event_unique UNIQUE (user_id, event_id)
);

CREATE INDEX review_logs_user_time_idx ON app.review_logs (user_id, created_at DESC);
CREATE INDEX review_logs_item_time_idx ON app.review_logs (review_item_id, created_at DESC);
