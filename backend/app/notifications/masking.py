import re

_PHONE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalise_phone(raw: str, default_country_code: str) -> str | None:
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = default_country_code + digits.lstrip("0")
    return digits if _PHONE.match(digits) else None


def mask(phone: str) -> str:
    return "•" * 4 + phone[-4:]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "••••"
    visible = local[:1] if local else ""
    return f"{visible}••••@{domain}"
