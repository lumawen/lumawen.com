# Lumawen app

The Flutter app: the web dashboard at lumawen.com/dashboard/ and, later, the Android app
(desktop and iOS after that). Licensed MIT (see LICENSE).

## Dashboard

- Sign in with email and password or Google, create an account, confirm it, reset a password.
- Admins: **Newsletters** (write, send yourself a test, send now or schedule), **Mailing list**
  (counts, search, CSV export, erase) and **Users**.

Routes live after the `#` (`/dashboard/#/confirm?token=...`), so tokens in email links never
reach server logs. The API is on the same site (`/api/`); signing in sets an HttpOnly cookie.

## Develop

Flutter version: see `FLUTTER_VERSION` (deploy and CI use the same one).

```sh
flutter pub get
flutter test
flutter build web --release --base-href /dashboard/ --no-web-resources-cdn
```

To try it against a local API, serve `build/web` at `/dashboard/` on the same origin as the API
(port 3040). `deploy/deploy.sh` builds it on the server and publishes it to
`/var/www/<host>/html/dashboard/`.
