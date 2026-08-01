"""Test data path resolution.

The client's 3Ddata directory is resolved from the ROSE_CLIENT_3DDATA
environment variable, falling back to the default checkout location under
the current user's home directory (USERPROFILE on Windows). No hardcoded
usernames in the repo.
"""
import os


def client_3ddata_root():
    """Root 3Ddata directory of the rose-offline-client checkout."""
    root = os.environ.get("ROSE_CLIENT_3DDATA")
    if root:
        return root
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "RustroverProjects", "rose-offline-client",
                        "target", "debug", "3Ddata")


def client_zone_dir():
    """Default test zone (JDT01). Override with ROSE_TEST_ZONE."""
    override = os.environ.get("ROSE_TEST_ZONE")
    if override:
        return override
    return os.path.join(client_3ddata_root(), "MAPS", "JUNON", "JDT01")


def client_terrain_tiles_dir():
    """Default terrain tile texture directory (Junon/JD)."""
    return os.path.join(client_3ddata_root(), "Terrain", "Tiles", "Junon", "JD")
