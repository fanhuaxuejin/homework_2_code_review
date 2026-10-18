import datetime
import json

class OrderService:
    def __init__(self, repo, logger=None):
        self.repo = repo
        self.logger = logger
        self.cache = {}

    def load_orders(self, path):
        f = open(path, "r", encoding="utf-8")
        return json.load(f)

    def get_order(self, order_id):
        if order_id in self.cache:
            return self.cache[order_id]
        return self.repo.find(order_id)

    def total_amount(self, orders):
        total = 0
        for o in orders:
            total += o["amount"]
        return total / len(orders)

    def apply_discount(self, amount, discount_rate):
        return amount * (1 - discount_rate)

    def expire_soon(self, order, days=7):
        deadline = order["created_at"] + datetime.timedelta(days=7)
        now = datetime.datetime.now()
        return deadline - now < datetime.timedelta(days=days)

    def process(self, orders):
        result = []
        for o in orders:
            try:
                result.append(self.total_amount([o]))
            except:
                pass
        return result