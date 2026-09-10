-- ---------------------------------------------------------------------------
-- 滚动窗口校验：源端聚合查询模板（PostgreSQL）
-- LD-01 8.3 · DD-61
--
-- 由本方以只读账号向 DS 展示库发起。计算在源库引擎内完成，仅回传聚合值，
-- 数据本体不出源库。DS 无需安装或运行任何程序。
--
-- 占位符由连接器按契约填充：
--   :src_table    源表全名
--   :ts_col       用于分区的时间列
--   :from / :to   窗口起止
--   列清单与序列化表达式按 meta.source_contract.definition 中声明的顺序生成，
--   不得使用表定义顺序——ALTER TABLE 会改变后者。
--
-- 序列化规则（不依赖任何会话默认值，见 LD-01 8.3）：
--   数值   to_char(v,'FM…0.000')  显式定标度，禁用裸 ::text
--   时间   AT TIME ZONE 'UTC' 后固定格式串
--   NULL   '\N' 哨兵，与空串区分
--   聚合   md5 两段 32 位切片各自求和，与行序无关；禁用 string_agg
-- ---------------------------------------------------------------------------

SELECT
    (:ts_col AT TIME ZONE 'Asia/Riyadh')::date                      AS day_key,
    count(*)                                                        AS row_count,
    sum(('x' || substr(md5(row_sig), 1, 8))::bit(32)::int::bigint)  AS hash_hi,
    sum(('x' || substr(md5(row_sig), 9, 8))::bit(32)::int::bigint)  AS hash_lo
FROM (
    SELECT
        :ts_col,
        concat_ws('|',
            -- 以下按契约声明的列顺序生成，示例形态：
            coalesce(id::text,                                          '\N'),
            coalesce(to_char(ts AT TIME ZONE 'UTC',
                             'YYYY-MM-DD HH24:MI:SS.US'),               '\N'),
            coalesce(to_char(amount, 'FM9999999999990.000'),            '\N'),
            coalesce(note,                                              '\N'),
            coalesce(flag::text,                                        '\N')
        ) AS row_sig
    FROM :src_table
    WHERE :ts_col >= :from
      AND :ts_col <  :to
) s
GROUP BY 1
ORDER BY 1;

-- 本方侧对 raw 副本执行完全相同的序列化与聚合逻辑，代码共用而非另写一份：
-- 两份实现迟早漂移，漂移之后所有差异都是假的。
