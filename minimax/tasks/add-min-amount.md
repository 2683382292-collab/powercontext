# 任务 add-min-amount

给 `filter_expenses` 增加最低金额筛选，并保持原有类别筛选。

要改什么：
- 实现 SPEC-AMT-001。传入 min_amount 时只保留 amount 大于或等于该值的记录，闭区间。
- min_amount 可以和 category 同时使用。

哪些原有行为必须保持：
- SPEC-CAT-001：category 相等筛选、省略 category 时保留全部、空列表返回空列表。
- SPEC-COMPAT-001：只按 category 调用的结果不变。

来源：
- spec SPEC-AMT-001 specs/amount.md
- spec SPEC-CAT-001 specs/category.md
- spec SPEC-COMPAT-001 specs/compat.md
- code filter_expenses sample_app/ledger.py
- test test_min_amount sample_app/tests/test_min_amount.py
- test test_category sample_app/tests/test_category.py
