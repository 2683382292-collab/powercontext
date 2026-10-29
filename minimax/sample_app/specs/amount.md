# SPEC-AMT-001 按最低金额筛选

标识：SPEC-AMT-001

`filter_expenses` 在传入 `min_amount` 时只保留 `amount` 大于或等于该值的记录。
金额下界按闭区间比较。
它可以和 `category` 同时生效。未传入 `min_amount` 时不按金额排除记录。
