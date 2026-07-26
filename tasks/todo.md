# Tasks: Seamless First-Time Admin Onboarding (P0-1)

- [ ] Add `admin_email` and `admin_password` to `Settings` in `src/potato/config.py` <!-- id: 0 -->
- [ ] Implement automatic admin seeding in `_init_accounts` inside `src/potato/main.py` <!-- id: 1 -->
- [ ] Update `deploy.sh` to write `ADMIN_EMAIL` to `.env` and display clean login instructions in summary <!-- id: 2 -->
- [ ] Update `.env.example` with `ADMIN_EMAIL` and `ADMIN_PASSWORD` documentation <!-- id: 3 -->
- [ ] Add unit test verifying auto-seeding of admin account from settings <!-- id: 4 -->
- [ ] Run test suite (`pytest` and `bash -n deploy.sh`) <!-- id: 5 -->
