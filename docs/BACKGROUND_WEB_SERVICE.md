# Web 서버를 macOS 백그라운드 서비스로 실행하기

`launchd`가 로그인 시 Web 서버를 시작하고, 비정상 종료되면 자동으로 다시 실행합니다.
기본 주소는 `http://127.0.0.1:57949`입니다.

```bash
./scripts/install_web_service.sh
./scripts/web_service_status.sh
```

다른 고정 포트를 쓰려면 설치 명령의 첫 번째 인자로 지정합니다.

```bash
./scripts/install_web_service.sh 58000
./scripts/web_service_status.sh 58000
```

코드를 수정한 뒤 실행 중인 서비스에 반영하려면 설치 명령을 다시 실행하면 됩니다.
`.env`도 프로세스가 시작될 때마다 다시 읽습니다.

로그는 `.runtime/web.stdout.log`와 `.runtime/web.stderr.log`에 기록됩니다.
서비스를 제거할 때는 다음 명령을 사용합니다.

```bash
./scripts/uninstall_web_service.sh
```
