"""Booking flow: hold a bike, then charge the customer through payments-gateway."""

import requests

PAYMENTS_URL = "http://payments-gateway.internal:8080"
HOLD_MINUTES = 10


def hold(bike_id: str) -> dict:
    return {"bike_id": bike_id, "hold_minutes": HOLD_MINUTES}


def charge(booking):
    return requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment())
