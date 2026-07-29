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

The install step creates one role: **Crema User**. It has Desk access, so a user with
this role can open Desk and use the Crema desk UI (the robot button on a list view, and
`Ask …` in the awesomebar) alongside System Managers. Give it to any user who should be
able to call Crema — through the Desk UI, or the HTTP endpoints directly.

The install step also seeds one **Crema Model Assignment** row per predefined
interface name onto **Crema Settings**, with no provider assigned yet — this is the
fixed row set the Model Assignments grid shows. `bench migrate` re-runs this seeding
step, so a name added to a future version of Crema appears without a manual step. It
also creates the three Number Cards the Crema workspace shows (calls, cost, blocked).

The install step also creates one Frappe user, `crema@<site>` — enabled, no password
(it can never log in), holding only the `Crema User` role. It is set as Crema Settings'
**Default Isolation User** unless that field already has a value. This is the sandbox
identity every interface uses by default; it exists so setting a Default Provider in
[configure.md](configure.md) is enough to make Crema usable, without also having to
create and fence a user by hand first.

The install step does not create a provider, or assign one to an interface. You do
that yourself. Go to [configure.md](configure.md) next.
