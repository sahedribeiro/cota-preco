"""Conservative product wording normalization; never infer missing specs."""
import re
from app.services.comparison_service import normalize


def canonical_unit(value: str | None) -> str:
    text = normalize(value or '')
    return 'unidade' if text in {'uni','un','und','unid','unidade','unidades','unids','pcs','pc','piece','pieces','ea','c62'} else text


def canonical_description(value: str) -> str:
    text = normalize(value)
    text = re.sub(r'(\d)\s*(gb|tb|mb)\b', r'\1\2', text)
    text = re.sub(r'\b(laptop|notebook|portatil)\b', 'portatil', text)
    text = re.sub(r'\b(memory|memoria)\s+ram\b', 'ram', text)
    pattern = r'\b(?:(ram|ssd|hdd)\s+(\d+(?:gb|tb|mb))|(\d+(?:gb|tb|mb))\s+(ram|ssd|hdd))\b'
    text = re.sub(pattern, lambda match: (match[1] or match[4])+'_'+(match[2] or match[3]), text)
    text = re.sub(r'\b(de|com|with|e|and)\b', ' ', text)
    # Keep order where bare numbers remain: their meaning can depend on position.
    tokens = text.split()
    return ' '.join(tokens if any(re.search(r'\d', token) and not re.fullmatch(r'(?:(ram|ssd|hdd)_\d+(gb|tb|mb)|i[3579])',token) for token in tokens) else sorted(tokens))
