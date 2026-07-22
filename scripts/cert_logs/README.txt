AIRBIZNESS x RATEHAWK (ETG API v3) — Certification test logs
Ticket: APIR-53193
Environment: SANDBOX  (host https://api-sandbox.worldota.net/api/b2b/v3)
API key id: 823
Test hotels: 10004834, 8819557
Generated: 2026-07-22

WHAT THIS PACKAGE CONTAINS
--------------------------
One JSON file per mandatory certification test case (ETG "Test cases" list).
Each file contains:
  - scenario, partner_order_id, params (occupancy / rooms / residency)
  - steps: outcome of each stage (hotelpage -> prebook -> booking/form ->
    booking/finish -> finish/status polling -> order/info)
  - final_status (ok / soldout / book_limit)
  - order (order_id, status, hotel, amount) for successful bookings
  - api_trace: the RAW request/response of EVERY API call, i.e. for each call:
        endpoint, method, partner_request (our body), http_status, etg_response

RESULTS — 7/7 PASS
------------------
File                              Scenario                                          Hotel      Result      Order
1_multiroom_mixed.json            Multi-room, mixed adults + children               10004834   ok          100037515
                                  (room1: 2 ad + 1 child 3y; room2: 2 ad + 3 children 1y/5y/17y)
2_booking_with_children.json      Booking with children (2 ad + 2 children 0y/17y)  10004834   ok          100037511
3_uzbekistan_citizenship.json     Booking with Uzbekistan citizenship (residency=uz)10004834   ok          100037513
4_price_increase_prebook.json     Rate price increase at prebook step (+10%)        8819557    ok          100037517
5_unknown_success.json            Successful booking after unknown errors           10004834   ok          100037521
                                  (partner_order_id ends "unknown_success"; kept polling finish/status until ok)
6_unknown_soldout.json            Failed booking -> soldout after unknown error     10004834   soldout     (no order - expected)
                                  (partner_order_id ends "unknown_soldout")
7_unknown_book_limit.json         Failed booking -> book_limit after unknown error  10004834   book_limit  (no order - expected)
                                  (partner_order_id ends "unknown_book_limit")

BOOKING FLOW IMPLEMENTED
------------------------
search/hp -> hotel/prebook -> hotel/order/booking/form ->
hotel/order/booking/finish -> hotel/order/booking/finish/status (polling) ->
hotel/order/info ; cancellation via hotel/order/cancel (verified separately).

Contact: pascal.repir@gmail.com (AirBizness OU)
