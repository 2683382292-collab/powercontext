import unittest

from ledger import filter_expenses

EXPENSES = (
    {"id": "a", "category": "travel", "amount": 20},
    {"id": "b", "category": "meals", "amount": 80},
    {"id": "c", "category": "travel", "amount": 5},
)


class MinAmountFilterTest(unittest.TestCase):
    def test_min_amount_is_inclusive(self):
        selected = filter_expenses(EXPENSES, min_amount=20)
        self.assertEqual([item["id"] for item in selected], ["a", "b"])

    def test_min_amount_combines_with_category(self):
        selected = filter_expenses(EXPENSES, category="travel", min_amount=10)
        self.assertEqual([item["id"] for item in selected], ["a"])


if __name__ == "__main__":
    unittest.main()
