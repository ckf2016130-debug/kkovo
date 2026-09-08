import os

from functools import partial

import pandas as pd
import requests


class MarketSourceError(RuntimeError):
    pass


class MarketClient:
    def __init__(self, token, url, timeout):
        self._token = token
        self._url = url
        self._timeout = timeout
        self._session = requests.Session()

    def query(self, api_name, fields='', **kwargs):
        response = self._session.post(self._url, json={
            'api_name': api_name, 'token': self._token,
            'params': kwargs, 'fields': fields,
        }, timeout=self._timeout, allow_redirects=False)
        if response.status_code in (401, 403):
            raise MarketSourceError(
                f'{api_name}: HTTP {response.status_code}; market source authentication or permission rejected. '
                'Check the configured service URL and repository TINYSHARE_TOKEN secret.')
        if response.status_code != 200:
            raise MarketSourceError(f'{api_name}: HTTP {response.status_code}')
        try:
            body = response.json()
        except ValueError:
            raise MarketSourceError(f'{api_name}: response is not JSON') from None
        if not isinstance(body, dict) or body.get('code') != 0:
            raise MarketSourceError(f'{api_name}: provider returned an error response')
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list):
            raise MarketSourceError(f'{api_name}: response has no valid data table')
        return pd.DataFrame(data['items'], columns=data['fields'])

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return partial(self.query, name)


def create_pro(timeout=30):
    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TINYSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not configured")
    return MarketClient(token, os.getenv(
        'TUSHARE_API_URL', 'https://fastapic.stockai888.top'), timeout)
