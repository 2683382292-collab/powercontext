# SPEC-COMPAT-001 保持原有类别行为

标识：SPEC-COMPAT-001

增加金额筛选后，只按 `category` 调用 `filter_expenses` 的结果必须与 SPEC-CAT-001 一致。
空列表仍然返回空列表。不得删除类别相等判断。
