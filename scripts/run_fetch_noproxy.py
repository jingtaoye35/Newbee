"""后台跑 fetch_data 的 wrapper: 关闭代理 + patch requests."""
import os
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

# 全局 patch requests Session proxies
import requests
_orig = requests.Session.__init__
def _init(self, *a, **kw):
    _orig(self, *a, **kw)
    self.proxies = {'http': '', 'https': ''}
requests.Session.__init__ = _init

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.fetch_data import main
    main()