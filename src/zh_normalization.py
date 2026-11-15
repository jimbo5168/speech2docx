"""Conservative Chinese spelling normalization, without term correction."""


def canonicalize_variants(text: str) -> str:
    """Unify the interchangeable verb spellings 叫作/叫做.

    Do not equate 作/做 or 坐/座 globally: those characters also distinguish
    real words. Homophones such as 堕/多 and 五欲/物欲 remain errors.
    """
    return text.replace("叫作", "叫做")
