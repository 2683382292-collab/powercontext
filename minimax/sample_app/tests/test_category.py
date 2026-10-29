import unittest

from ledger import filter_expenses

EXPENSES = (
    {"id": "a", "category": "travel", "amount": 20},
    {"id": "b", "category": "meals", "amount": 80},
    {"id": "c", "category": "travel", "amount": 5},
)


class CategoryFilterTest(unittest.TestCase):
    def test_category_keeps_matching_rows(self):
        selected = filter_expenses(EXPENSES, category="travel")
        self.assertEqual([item["id"] for item in selected], ["a", "c"])

    def test_omitted_category_keeps_every_row(self):
        self.assertEqual(filter_expenses(EXPENSES), list(EXPENSES))

    def test_empty_input_stays_empty(self):
        self.assertEqual(filter_expenses((), category="travel"), [])

    def test_omitted_min_amount_does_not_drop_rows(self):
        selected = filter_expenses(EXPENSES, category="meals", min_amount=None)
        self.assertEqual([item["id"] for item in selected], ["b"])


if __name__ == "__main__":
    unittest.main()
