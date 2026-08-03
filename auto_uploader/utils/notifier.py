"""Cross-platform desktop notifications (Windows toast / macOS / libnotify),
best-effort only - a notification failure should never break an upload."""


def notify(title: str, message: str, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        from plyer import notification

        # Windows' balloon-tip API hard-caps these fields (256 chars for the
        # message, 64 for the title) and raises ValueError past that rather
        # than truncating - a long error message passed straight through
        # crashed the notification thread mid-run. Truncate before sending.
        notification.notify(
            title=title[:60],
            message=message[:240],
            timeout=8,
            app_name="AutoUploader",
        )
    except Exception:
        pass
