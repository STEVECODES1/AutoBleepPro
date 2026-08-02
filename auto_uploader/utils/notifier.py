"""Cross-platform desktop notifications (Windows toast / macOS / libnotify),
best-effort only - a notification failure should never break an upload."""


def notify(title: str, message: str, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=8, app_name="AutoUploader")
    except Exception:
        pass
