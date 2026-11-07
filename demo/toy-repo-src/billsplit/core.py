def split_bill(total, people):
    """Split ``total`` evenly between ``people`` friends."""
    if people <= 0:
        return []
    share = round(total / people, 2)
    return [share] * people
