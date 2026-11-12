"""Read the package identifier of a captured mobile application."""


def mobile_package_name(target):
    return str(target.get("package_name") or target.get("package") or target.get("bundle_id")
               or str(target.get("id") or "").rsplit(":", 1)[-1]).strip()


