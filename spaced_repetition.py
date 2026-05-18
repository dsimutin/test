from datetime import date, timedelta


def sm2(quality: int, ease_factor: float, interval: int, repetitions: int):
    """
    SM-2 algorithm. quality: 0-5 (< 3 = failed).
    Returns (new_ease_factor, new_interval, new_repetitions).
    """
    if quality < 3:
        return max(1.3, ease_factor - 0.2), 1, 0

    new_repetitions = repetitions + 1
    if repetitions == 0:
        new_interval = 1
    elif repetitions == 1:
        new_interval = 6
    else:
        new_interval = round(interval * ease_factor)

    new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    return max(1.3, new_ef), new_interval, new_repetitions


def next_review_date(interval: int) -> str:
    return (date.today() + timedelta(days=interval)).isoformat()
