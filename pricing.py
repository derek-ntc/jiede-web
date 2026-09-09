from decimal import Decimal, InvalidOperation


SUPPORTED_CURRENCIES = {
    "CNY": 2,
    "USD": 2,
    "EUR": 2,
    "HKD": 2,
    "GBP": 2,
    "JPY": 0,
}

SQLITE_INTEGER_MAX = 2**63 - 1
MAX_PRICED_QUANTITY = 2_147_483_647
MAX_UNIT_PRICE_MINOR = SQLITE_INTEGER_MAX // MAX_PRICED_QUANTITY


def normalize_currency(raw: str) -> str:
    currency = str(raw or "CNY").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("请选择有效币种")
    return currency


def parse_money_minor(raw: str, currency: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None

    currency = normalize_currency(currency)
    digits = SUPPORTED_CURRENCIES[currency]
    quantum = Decimal(1).scaleb(-digits)
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("价格格式不正确") from error
    if not value.is_finite():
        raise ValueError("价格格式不正确")
    if value < 0:
        raise ValueError("产品价格不能为负数")
    if value > Decimal(MAX_UNIT_PRICE_MINOR).scaleb(-digits):
        raise ValueError("产品价格过大")
    try:
        quantized = value.quantize(quantum)
    except InvalidOperation as error:
        raise ValueError("价格格式不正确") from error
    if value != quantized:
        raise ValueError(f"价格最多保留 {digits} 位小数")
    return int(value * (10**digits))


def format_money_minor(value: int | None, currency: str) -> str:
    if value is None:
        return ""
    currency = normalize_currency(currency)
    digits = SUPPORTED_CURRENCIES[currency]
    return f"{Decimal(value).scaleb(-digits):.{digits}f}"


def line_total_minor(unit_price_minor: int | None, quantity: int) -> int | None:
    if quantity < 0:
        raise ValueError("数量不能为负数")
    if quantity == 0:
        return 0
    if unit_price_minor is None:
        return None
    total = unit_price_minor * quantity
    if total > SQLITE_INTEGER_MAX:
        raise ValueError("金额过大")
    return total
