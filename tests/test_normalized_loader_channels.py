from eligibility.catalog.normalized_loader import _channels
from eligibility.schema.enums import SubscriptionChannel


def test_normalized_loader_preserves_known_channel_aliases() -> None:
    assert _channels(
        [
            "MOBILE_APP",
            "MOBILE_WEB",
            "INTERNET",
            "BRANCH",
            "TELEPHONE",
            "CALL_CENTER",
            "TABLET",
            "OTHER",
        ]
    ) == [
        SubscriptionChannel.MOBILE,
        SubscriptionChannel.WEB,
        SubscriptionChannel.BRANCH,
        SubscriptionChannel.CALL_CENTER,
        SubscriptionChannel.OTHER,
    ]
