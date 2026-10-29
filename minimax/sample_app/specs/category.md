# SPEC-CAT-001 按类别筛选

标识：SPEC-CAT-001

`filter_expenses` 在传入 `category` 时只保留 `category` 字段相等的记录。
不传 `category` 时保留全部记录。输入为空列表时返回空列表。
