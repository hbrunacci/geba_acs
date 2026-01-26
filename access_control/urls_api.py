from django.urls import path

from access_control.api.v1.api_views import (
    BioStarDeviceListAPI,
    BioStarDeviceSyncAPI,
    BioStarDeviceUsersAPI,
    BioStarUserListAPI,
    BioStarUserSearchAPI,
    BioStarUserSyncAPI,
    ExternalAccessLogSyncAPI,
    WhitelistBatchCreateAPI,
)

urlpatterns = [
    path("biostar/devices/", BioStarDeviceListAPI.as_view(), name="biostar_devices_list_api"),
    path("biostar/devices/sync/", BioStarDeviceSyncAPI.as_view(), name="biostar_devices_sync_api"),
    path(
        "biostar/devices/<int:device_id>/users/",
        BioStarDeviceUsersAPI.as_view(),
        name="biostar_device_users_api",
    ),
    path("biostar/users/", BioStarUserListAPI.as_view(), name="biostar_users_list_api"),
    path("biostar/users/search/", BioStarUserSearchAPI.as_view(), name="biostar_users_search_api"),
    path("biostar/users/sync/", BioStarUserSyncAPI.as_view(), name="biostar_users_sync_api"),
    path(
        "external-access/sync/",
        ExternalAccessLogSyncAPI.as_view(),
        name="external_access_sync_api",
    ),
    path(
        "whitelist/batch/",
        WhitelistBatchCreateAPI.as_view(),
        name="whitelist_batch_create_api",
    ),
]
