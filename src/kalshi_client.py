"""Authenticated client for the Kalshi Trading API v2.

Handles RSA-PSS request signing, retries with exponential backoff, and
exposes typed methods for the market data and trading endpoints used by the
rest of the bot.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class KalshiAPIError(Exception):
    """Raised for non-retryable Kalshi API errors (4xx responses)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Kalshi API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class KalshiRetryableError(KalshiAPIError):
    """Raised for retryable Kalshi API errors (429 and 5xx responses)."""


class KalshiClient:
    """Authenticated wrapper around the Kalshi Trading API v2."""

    def __init__(
        self,
        api_key_id: str | None = None,
        private_key_pem: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            api_key_id: Kalshi API key ID. Defaults to the
                ``KALSHI_API_KEY_ID`` environment variable.
            private_key_pem: RSA private key in PEM format. Defaults to the
                ``KALSHI_PRIVATE_KEY`` environment variable.
            base_url: API base URL. Defaults to the ``KALSHI_BASE_URL``
                environment variable, or the production API if unset.

        Raises:
            ValueError: If the API key ID or private key are missing.
        """
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID")
        pem = private_key_pem or os.environ.get("KALSHI_PRIVATE_KEY")
        self.base_url = (base_url or os.environ.get("KALSHI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        if not self.api_key_id:
            raise ValueError("KALSHI_API_KEY_ID is not set")
        if not pem:
            raise ValueError("KALSHI_PRIVATE_KEY is not set")

        self._private_key: RSAPrivateKey = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None
        )
        self._session = requests.Session()
        pool_size = int(os.environ.get("ORDER_BOOK_FETCH_WORKERS", "20"))
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _sign(self, method: str, signing_path: str) -> dict[str, str]:
        """Build the Kalshi authentication headers for a request.

        Args:
            method: HTTP method (e.g. ``"GET"``).
            signing_path: URL path (including the ``/trade-api/v2`` prefix,
                excluding query string and host) to sign.

        Returns:
            The ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-TIMESTAMP``, and
            ``KALSHI-ACCESS-SIGNATURE`` headers.
        """
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{signing_path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }

    @retry(
        retry=retry_if_exception_type((KalshiRetryableError, requests.exceptions.RequestException)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request to the Kalshi API.

        Args:
            method: HTTP method (e.g. ``"GET"``, ``"POST"``, ``"DELETE"``).
            path: Endpoint path relative to the API base URL (e.g.
                ``"/markets"``).
            params: Optional query parameters.
            json_body: Optional JSON request body.

        Returns:
            The parsed JSON response body.

        Raises:
            KalshiRetryableError: On 429 or 5xx responses (retried
                automatically with exponential backoff).
            KalshiAPIError: On other non-2xx responses.
        """
        url = f"{self.base_url}{path}"
        signing_path = urlparse(url).path
        headers = self._sign(method, signing_path)

        response = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=10,
        )

        if response.status_code == 429 or response.status_code >= 500:
            raise KalshiRetryableError(response.status_code, response.text)
        if response.status_code >= 400:
            raise KalshiAPIError(response.status_code, response.text)

        if not response.content:
            return {}
        return response.json()

    def get_market(self, ticker: str) -> dict[str, Any]:
        """Fetch a single market by ticker.

        Args:
            ticker: The market ticker symbol.

        Returns:
            The market dict (fields: ``ticker``, ``title``, ``close_time``, etc.).
        """
        response = self._request("GET", f"/markets/{ticker}")
        return response.get("market", response)

    def get_markets(self, **filters: Any) -> dict[str, Any]:
        """List markets, optionally filtered.

        Args:
            **filters: Query parameters such as ``status``, ``event_ticker``,
                ``series_ticker``, ``limit``, or ``cursor``.

        Returns:
            JSON response containing a ``markets`` list and pagination cursor.
        """
        return self._request("GET", "/markets", params=filters)

    def get_events(self, **filters: Any) -> dict[str, Any]:
        """List events, optionally filtered.

        Args:
            **filters: Query parameters such as ``status``, ``series_ticker``,
                ``limit``, or ``cursor``.

        Returns:
            JSON response containing an ``events`` list and pagination cursor.
        """
        return self._request("GET", "/events", params=filters)

    def get_market_orderbook(self, ticker: str) -> dict[str, Any]:
        """Fetch the order book for a market.

        Args:
            ticker: The market ticker symbol.

        Returns:
            JSON response containing ``yes`` and ``no`` bid price levels.
        """
        return self._request("GET", f"/markets/{ticker}/orderbook")

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a new order.

        Args:
            payload: Order parameters as required by the Kalshi API (e.g.
                ``ticker``, ``action``, ``side``, ``count``, ``type``,
                ``yes_price``/``no_price``, ``client_order_id``).

        Returns:
            JSON response describing the created order.
        """
        return self._request("POST", "/portfolio/orders", json_body=payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order.

        Args:
            order_id: The ID of the order to cancel.

        Returns:
            JSON response confirming the cancellation.
        """
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_balance(self) -> dict[str, Any]:
        """Fetch the account balance.

        Returns:
            JSON response containing the account balance in cents.
        """
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, **filters: Any) -> dict[str, Any]:
        """List current positions.

        Args:
            **filters: Query parameters such as ``ticker``, ``event_ticker``,
                ``limit``, or ``cursor``.

        Returns:
            JSON response containing ``market_positions`` and ``event_positions``.
        """
        return self._request("GET", "/portfolio/positions", params=filters)
