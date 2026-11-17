"""Sources hosted on Gitee, the code host most used in China.

Gitee answers questions about one repository and its releases with no account. Its search does not,
so a find is added to a pack by hand, like any other line, and resolved here every month.
"""

import re

from . import resolvers
from .resolvers import ResolveError
from .util import FetchError

API = "https://gitee.com/api/v5/repos/%s/releases/%s"


def r_gitee(src, net):
    """Newest Gitee release. assets maps platform to a regex; asset is a single regex."""
    repo = src["repo"]
    wanted = src.get("assets") or ({"any": src["asset"]} if src.get("asset") else {})
    try:
        rel = net.json(API % (repo, "tags/" + src["tag"] if src.get("tag") else "latest"))
    except FetchError as e:
        raise ResolveError("cannot read the releases of %s on Gitee: %s" % (repo, e))
    if not isinstance(rel, dict) or not rel.get("tag_name"):
        raise ResolveError("%s has no release on Gitee" % repo)
    tag = rel["tag_name"]
    assets = [a for a in rel.get("assets") or [] if a.get("name") and a.get("browser_download_url")]
    names = [a["name"] for a in assets][:20]
    files, missing = [], []
    for platform, pattern in wanted.items():
        hit = next((a for a in assets if re.search(pattern, a["name"])), None)
        if hit is None:
            missing.append(platform)
            continue
        f = {"url": hit["browser_download_url"], "filename": hit["name"]}
        if platform != "any":
            f["platform"] = platform
        files.append(f)
    if not files:
        raise ResolveError("no asset in %s %s matched %s; assets: %s" % (repo, tag, list(wanted.values()), names))
    res = {"version": re.sub(r"^[vV](?=\d)", "", tag), "date": (rel.get("created_at") or "")[:10] or None,
           "files": files}
    if missing:
        res["warnings"] = ["no asset for %s; assets: %s" % (", ".join(missing), names)]
    return res


resolvers.RESOLVERS["gitee"] = r_gitee
