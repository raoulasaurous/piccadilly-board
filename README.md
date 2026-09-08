# Piccadilly line board

A live London Underground departure board for one station, built to run full
screen on a wall-mounted tablet. One HTML file, no build step, no server.

Times come from the [TfL Unified API](https://api-portal.tfl.gov.uk/), which is
free and needs no key at this volume. The board refreshes every 30 seconds and
shows sample times if it cannot reach the network, labelled as such.

## Change the station

Edit the CONFIG block near the top of the script in `index.html`:

    const LINE       = "piccadilly";
    const STATION_ID = "940GZZLUASL";   // Arsenal
    const STATION    = "Arsenal";

Find another station's id:

    https://api.tfl.gov.uk/StopPoint/Search/{name}?modes=tube
