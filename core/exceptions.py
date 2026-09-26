class ERPError(Exception):
    """Base error for business rule violations."""


class InsufficientStockError(ERPError):
    def __init__(self, product, warehouse, requested, available):
        self.product = product
        self.warehouse = warehouse
        self.requested = requested
        self.available = available
        super().__init__(
            f"Không đủ tồn kho {product} tại {warehouse}: "
            f"cần {requested}, khả dụng {available}"
        )


class InvalidStatusError(ERPError):
    """Raised when an action is not allowed for the document's current status."""
