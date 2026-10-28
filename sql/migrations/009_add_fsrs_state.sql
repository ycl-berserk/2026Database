-- K3：为复习项保存 FSRS 记忆状态，并为事件流水记录评分与算法。
-- 旧调用（interval_days 翻倍）仍作为 REVIEW_ALGORITHM=simple 的降级路径保留。

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'app'
           AND table_name = 'review_items'
           AND column_name = 'fsrs_stability'
    ) THEN
        ALTER TABLE app.review_items
            ADD COLUMN fsrs_stability double precision NOT NULL DEFAULT 1.0,
            ADD COLUMN fsrs_difficulty double precision NOT NULL DEFAULT 5.0,
            ADD COLUMN fsrs_lapses integer NOT NULL DEFAULT 0;
        -- 旧数据用当前间隔作为稳定性起点，避免升级后第一次复习被重置。
        UPDATE app.review_items
           SET fsrs_stability = GREATEST(interval_days::double precision, 0.5);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'review_items_fsrs_stability_check'
    ) THEN
        ALTER TABLE app.review_items
            ADD CONSTRAINT review_items_fsrs_stability_check
                CHECK (fsrs_stability > 0),
            ADD CONSTRAINT review_items_fsrs_difficulty_check
                CHECK (fsrs_difficulty BETWEEN 1 AND 10),
            ADD CONSTRAINT review_items_fsrs_lapses_check
                CHECK (fsrs_lapses >= 0);
    END IF;
END;
$$;

ALTER TABLE app.review_logs
    ADD COLUMN IF NOT EXISTS rating varchar(10),
    ADD COLUMN IF NOT EXISTS algorithm varchar(10);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'review_logs_rating_check'
    ) THEN
        ALTER TABLE app.review_logs
            ADD CONSTRAINT review_logs_rating_check
                CHECK (rating IS NULL OR rating IN ('again', 'hard', 'good', 'easy')),
            ADD CONSTRAINT review_logs_algorithm_check
                CHECK (algorithm IS NULL OR algorithm IN ('fsrs', 'simple'));
    END IF;
END;
$$;
