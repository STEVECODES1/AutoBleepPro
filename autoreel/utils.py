"""Small shared helpers used across the AutoReel package."""


def format_timestamp(seconds: float) -> str:
    """Convert seconds to an HH:MM:SS (or MM:SS) string."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
