<div align="center">

# infra-server

Общий Caddy и VPN-выход для небольшого сервера с проектами.

[![Deploy](https://github.com/aimuzov-projects/infra-server/actions/workflows/deploy.yml/badge.svg)](https://github.com/aimuzov-projects/infra-server/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Caddy 2.11](https://img.shields.io/badge/Caddy-2.11-1f88c0?logo=caddy&logoColor=white)
![Xray](https://img.shields.io/badge/Xray-26.9-555)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ed?logo=docker&logoColor=white)

[English](README.md) · **Русский**

</div>

---

Один Caddy держит 80/443, выпускает сертификаты и раскидывает домены по контейнерам проектов.
Проекты не публикуют порты и не знают друг о друге: какой домен куда идёт, знает только
[`sites/`](sites).

```mermaid
flowchart LR
    user([Интернет]) -->|80 / 443| caddy[Caddy]
    subgraph server[сервер с проектами]
        caddy -->|сеть edge| apps[контейнеры проектов]
        apps -.->|сеть vpn, по желанию| vpn[Xray-прокси]
    end
    vpn -.-> api([OpenRouter, Telegram, ...])
```

## Сайты

| Домен | Проект | Контейнеры |
| --- | --- | --- |
| [tatiana-shkatova.ru](https://tatiana-shkatova.ru) | [aimuzov-projects/shkatova-psychologist](https://github.com/aimuzov-projects/shkatova-psychologist) | `shkatova-psychologist`, `shkatova-chat` |
| [taplist.aimuzov.online](https://taplist.aimuzov.online) | [aimuzov-projects/community-taplist](https://github.com/aimuzov-projects/community-taplist) | `community-taplist`, `community-taplist-api` |
| [codenames.aimuzov.online](https://codenames.aimuzov.online) | [aimuzov-projects/codenames](https://github.com/aimuzov-projects/codenames) | `codenames`, `codenames-relay` |
| [logist.aimuzov.online](https://logist.aimuzov.online) | [aimuzov-projects/prestige-logist](https://github.com/aimuzov-projects/prestige-logist) | `prestige-logist`, `prestige-logist-api` |
| [sublya.aimuzov.online](https://sublya.aimuzov.online) | [aimuzov-projects/sublya](https://github.com/aimuzov-projects/sublya) | `sublya-site`, `sublya-tunnel` |
| [lazyvimx.aimuzov.online](https://lazyvimx.aimuzov.online) | [lazyvimx/site](https://github.com/lazyvimx/site) | `lazyvimx-site` |

## Что где лежит

```
Caddyfile                 общий сниппет (сжатие, заголовки безопасности) и импорт sites/
sites/<project>.caddy     по файлу на сайт: домен → контейнер
compose.yml               сервисы caddy и vpn
deploy/remote.sh          выкладка, выполняется на сервере
deploy/site.sh            выкладка любого сайта: запускает site-deploy@<project> и показывает его лог
deploy/site-build.sh      сама сборка, внутри site-deploy@.service
ssh/10-no-passwords.conf  sshd: только ключи, без паролей; ставится при каждой выкладке
vpn/update.sh             собирает конфиг Xray из подписки
vpn/vpn-update.{service,timer}   обновление конфига раз в сутки
docker/docker-prune.{service,timer}   чистка кэша сборки Docker раз в неделю
.env.example              образец секретов (настоящий .env есть только на сервере)
```

## Как устроен сервер

```
/srv/
  infra-server/     ← этот репо
  <project>/        ← клон репо проекта, там же собирается и запускается
```

- Репо называются `<пространство>-<проект>`: `infra-server`, `infra-site-template`,
  `infra-site-kit`, `shkatova-psychologist`. Папка на сервере повторяет имя репо.
- Один проект -- одна папка `/srv/<project>` и один compose-проект с явным `name:`. Контейнеры,
  тома и сети получают префикс проекта, поэтому проекты не пересекаются.
- Проекты не публикуют порты на хост. Они подключаются к внешней сети `edge`, Caddy ходит к ним
  по имени контейнера.
- Сайты выкладываются одним скриптом, [`deploy/site.sh`](deploy/site.sh), ключ сайта запускает его
  с именем проекта и тегом релиза, который Actions передаёт командой ssh (без тега -- свежий `main`).
  Сборка идёт в systemd-юните `site-deploy@<project>`, поэтому обрыв SSH её не
  останавливает, а `site.sh` транслирует её лог в Actions. Lock `/tmp/sites-deploy.lock` даёт серверу
  собирать по одному сайту, поэтому мерж сразу в несколько репо не собирает их параллельно на
  маленьком сервере. Лог последних выкладок: `journalctl -u site-deploy@<project>`.
- Каждый репо, публичный тоже, тянется на сервер по SSH своим read-only deploy key. В
  организации должны быть разрешены deploy keys (`deploy_keys_enabled_for_repositories`).

## Выкладка

Пуш в `main` запускает GitHub Actions: сначала `shellcheck` и `caddy validate`, затем вход по SSH
на сервер. Там ключ привязан к [`deploy/remote.sh`](deploy/remote.sh): он подтягивает `main`,
обновляет VPN-выход и применяет конфиг Caddy. Битый конфиг работающий не заменит. Повторить
выкладку без пуша:

```bash
gh workflow run deploy.yml
```

Ключ в `authorized_keys` сервера ограничен одной командой:

```
command="git -C /srv/infra-server pull --ff-only --quiet && /srv/infra-server/deploy/remote.sh",restrict ssh-ed25519 ... github-actions@infra-server
```

Приватная половина лежит в секрете `DEPLOY_SSH_KEY` репозитория.

## Секреты

В git секретов нет. Они лежат в `/srv/infra-server/.env` (образец --
[`.env.example`](.env.example)):

| Переменная | Кто читает |
| --- | --- |
| `VPN_SUBSCRIPTION_URL` | `vpn/update.sh` -- подписка, из которой собирается конфиг Xray |
| `TAPLIST_EDITOR_HASH` | `sites/community-taplist.caddy` -- bcrypt-хеш для `basic_auth` редактора |

Свой секрет сайту: сослаться на него как `{$NAME}` в `sites/<project>.caddy` и передать сервису
`caddy` в `compose.yml` поштучно, чтобы остальной `.env` в Caddy не попадал:

```yaml
environment:
  NAME: ${NAME:?NAME is not set, see .env.example}
```

Значения с `$` (bcrypt-хеши) -- в одинарных кавычках. Хеш пароля:

```bash
docker run --rm -it caddy:2.11-alpine caddy hash-password
```

## Новый проект

1. В compose проекта: `name: <project>`, у каждого сервиса уникальный `container_name`, сеть
   `edge` как `external: true`, без `ports:`.
2. Deploy key на сервере:

   ```bash
   ssh projects 'ssh-keygen -q -t ed25519 -N "" -C "projects-server:<project>" -f ~/.ssh/<project> &&
     printf "Host github-<project>\n\tHostName github.com\n\tUser git\n\tIdentityFile ~/.ssh/<project>\n\tIdentitiesOnly yes\n\n" >> ~/.ssh/config &&
     cat ~/.ssh/<project>.pub'
   ```

   Публичный ключ -- в репо:

   ```bash
   gh repo deploy-key add key.pub -R <org>/<repo> --title projects-server
   ```

3. Клонировать и поднять проект:

   ```bash
   ssh projects git clone github-<project>:<org>/<repo>.git /srv/<project>
   ```

4. Для сайта -- пустить выкладку из его GitHub Actions: публичную половину нового ключа дописать в
   `/root/.ssh/authorized_keys`, приватную положить в секрет репо `DEPLOY_SSH_KEY`:

   ```
   command="/srv/infra-server/deploy/site.sh <project>",restrict ssh-ed25519 ... github-actions@<project>
   ```

5. Добавить `sites/<project>.caddy` с `import common` и `reverse_proxy <container_name>:<port>`,
   направить DNS домена на сервер и запушить `main`.

## VPN-выход

Сервер стоит в РФ, и часть внешних API (OpenRouter, Telegram) отсюда недоступна. Для этого есть
контейнер `vpn`: Xray с HTTP-прокси `vpn:3128` и SOCKS `vpn:1080` на профиле "Всё и сразу" из
подписки. Маршруты профиля остаются как есть: российские адреса идут напрямую, остальное через
европейские ноды, а если они лежат -- через запасные цепочки.

Проект подключается по желанию, в своём compose:

```yaml
services:
  app:
    environment:
      HTTPS_PROXY: http://vpn:3128
      NODE_USE_ENV_PROXY: '1' # только для Node 24+: без этого fetch прокси не видит
    networks:
      - vpn

networks:
  vpn:
    name: vpn
    external: true
```

Конфиг пересобирает `vpn/update.sh` на каждой выкладке и раз в сутки по `vpn-update.timer`.
Проверка с сервера:

```bash
docker run --rm --network vpn curlimages/curl -sx http://vpn:3128 https://www.cloudflare.com/cdn-cgi/trace
```

## Сервер с нуля

1. Docker Engine из apt-репозитория docker.com и `apt install jq`.
2. Файрвол: `ufw allow 22/tcp`, `ufw allow 80/tcp`, `ufw allow 443` (TCP и UDP для HTTP/3).
3. Сети: `docker network create edge` и `docker network create vpn`.
4. Deploy key для этого репо (см. [Новый проект](#новый-проект)), затем
   `git clone github-infra-server:aimuzov-projects/infra-server.git /srv/infra-server`.
5. `/srv/infra-server/.env` по образцу [`.env.example`](.env.example), затем один раз
   `deploy/remote.sh`.
6. Оболочка: root получает fish из [dotfiles](https://github.com/aimuzov/dotfiles/blob/main/docs/fish/README.ru.md#linux-сервер),
   bash остаётся login shell, а интерактивный вход по SSH переключается в fish. Поставить
   `chezmoi_<version>_linux_amd64.deb` из [релизов chezmoi](https://github.com/twpayne/chezmoi/releases),
   сверив его с `checksums.txt`, затем
   `chezmoi init --apply --recurse-submodules=false https://github.com/aimuzov/dotfiles.git`.
   Дальше каждый деплой этого репо делает `chezmoi update`, так что правки dotfiles приезжают
   со следующим деплоем (или `gh workflow run deploy.yml`).

## Лицензия

[MIT](LICENSE)
