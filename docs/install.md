# Install Crema

## Before you start

Set the environment variable `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` before you start
bench. `litellm` and `pydantic_core` need this variable on Python 3.14. The bench
virtual environment sets it automatically when you activate it.

## Steps

1. Get the app onto your bench:

   ```bash
   bench get-app crema <repository-url>
   ```

2. Install the app on your site:

   ```bash
   bench --site <your-site> install-app crema
   ```

3. Open the Desk. Find **Crema** in the app list. The icon is a robot — opening it
   lands you directly on **Crema Settings**.

## What the install step does

The install step creates one role: **Crema User**. The role has Desk access. A user
with this role can open Desk and use the Crema desk UI — the robot button on a list
view, and `Ask …` in the search bar — alongside System Managers. Give the role to any
user who calls Crema, through the desk UI or through the HTTP endpoints.

The install step also seeds one **Crema Model Assignment** row per interface name
onto **Crema Settings**, with no provider assigned yet. The row set is not fixed: it
is the predefined interfaces plus any interface another installed app registers
through the `crema_interfaces` hook (see [configure.md](configure.md)). `bench
migrate` re-runs this seeding step, so a name added by a future version of Crema, or
by a newly installed app, appears without a manual step. It also re-stamps the
options of the Use Case dropdown on Crema Automation Task, so a name added there
shows up in that dropdown too. The install step also creates the three Number Cards
the Crema workspace shows (calls, cost, blocked).

The install step also creates one Frappe user, `crema@<site>` — enabled, no password
(it can never log in), holding only the `Crema User` role. If the site name is not a
valid email domain (for example `mysite`, which has no dot, or `test_site`, which has
an illegal character), the install step uses `crema@<site>.localhost` instead, with
each illegal character changed to a hyphen. The seeding step sets it
as Crema Settings' **Default Runs-As User** unless that field already has a value.
This is the isolation user every interface uses by default. It exists so that one
Default Provider in [configure.md](configure.md) makes Crema usable — you do not
have to create and fence a user by hand first. An isolation user must not be
`Administrator`, must not hold the `System Manager` role, and must not be a disabled
user — Crema rejects all three at save time.

The install step does not create a provider, or assign one to an interface. You do
that yourself. Go to [configure.md](configure.md) next.
