# edge-device-kit

현장 장비와 통신하는 코드에서 **실제로 문제가 됐던 부분만 골라** 정리한 리포지토리입니다.
직접 개발해 현장에서 운영 중인 두 시스템에서 핵심 모듈을 발췌·재구성했고,
같은 로직을 C++17로 옮긴 뒤 Python 원본과 **입력 단위로 대조 검증**합니다.

> Protocol pieces (Modbus RTU, vendor RS-485 frames, Sensirion I2C) extracted from two
> production edge systems, ported to C++17 with AI assistance, and verified against the
> Python originals with differential tests, sanitizers, fuzzing and a virtual serial device.

## 출처가 된 시스템

| 시스템 | 무엇 | 규모 |
|---|---|---|
| **BeanPal** | 커피 로스터 제어·기록 데스크톱 앱 (FastAPI + React/Tauri) | 89개 브랜드 213개 장비 프리셋, 16종 통신 방식(Modbus RTU/TCP, S7, 시리얼, BLE, WebSocket 등) |
| **AgroKeep** | 농산물 저장 실험 챔버 데이터 수집 시스템 (정부 R&D 실험 환경) | Raspberry Pi 엣지 4대, I2C·RS-485 센서·저울·카메라·액추에이터, 무인 연속 운전 |

두 시스템의 전체 소스는 비공개입니다. 이 리포에는 **장비 통신과 장애 대응 부분만** 독립 실행 가능하게
정리해 담았습니다. 전체 구조는 [docs/architecture.md](docs/architecture.md)에 그림과 글로 설명합니다.

## 들어 있는 것

| 경로 | 내용 |
|---|---|
| `python/edgekit/modbus_rtu.py` | Modbus RTU 0x03/0x06 코덱 + 슬레이브 시뮬레이터 (공개 사양 기준, 엄격한 응답 검증) |
| `python/edgekit/lenframe.py` | 길이 접두형 벤더 RS-485 프레임 + **바이트 스트림 재동기화 디코더** |
| `python/edgekit/sensirion.py` | SCD41(CO₂)·SHT45(온습도) 드라이버 — 미준비 표본 거부, 센서 리셋 자가 복구, 적용값 되읽기 |
| `python/edgekit/transport.py` | 루프백 / 실제 시리얼 전송, USB 재열거 시 죽은 핸들 자동 재오픈 |
| `python/edgekit/reader.py` | 채널 리더 — 실패하면 **값 없음(None)**, 절대 대체값을 만들지 않음 |
| `python/edgekit/outbox.py` | SQLite WAL 저장 후 전송(store-and-forward) 버퍼 |
| `python/edgekit/sim_pty.py` | 가상 시리얼 로스터 (의사 터미널 + 열 모델) — 하드웨어 없이 통합 테스트 |
| `cpp/` | 위 통신 계층의 C++17 이식본, POSIX 시리얼, `edgekit_poll` CLI |
| `tests_cross/` | C++ ↔ Python 차등 테스트, C++ CLI ↔ Python 가상 장비 통합 테스트 |

## 설계 원칙 — 현장에서 배운 것

1. **없는 값을 만들지 않는다.** 센서가 "준비 안 됨"일 때 돌려주는 0은 CRC를 통과합니다.
   그것을 측정값으로 저장하면 그래프·평균·학습 데이터가 조용히 오염됩니다.
   실패는 `None` / `std::nullopt`로 드러내고, -1 같은 센티넬 값도 쓰지 않습니다.
2. **실패가 정상 경로다.** 끊김·부분 프레임·CRC 오류·잡음·지연을 결함 주입으로 매번 재현하고,
   "다음 주기에 스스로 회복하는가"까지 테스트합니다.
3. **같은 검증을 두 언어에.** C++ 이식본은 Python 원본과 수천 개의 생성 입력에서 같은 답을 내야 통과합니다.

현장 사례는 [docs/field-notes.md](docs/field-notes.md)에 정리했습니다.

## 실행

```bash
# Python (3.10+)
pip install pytest hypothesis pyserial
cd python && pytest -q

# C++ (CMake 3.20+, GCC 또는 Clang) — 주소·미정의 동작 검사기 켜고 빌드
cmake -S cpp -B cpp/build -DEDGEKIT_SANITIZE=ON && cmake --build cpp/build -j
ctest --test-dir cpp/build --output-on-failure

# C++ ↔ Python 대조 (릴리스 빌드 필요)
cmake -S cpp -B cpp/build-rel -DEDGEKIT_TESTS=OFF && cmake --build cpp/build-rel -j
EDGEKIT_BUILD=cpp/build-rel pytest -q tests_cross

# 가상 로스터를 띄우고 C++ CLI로 읽기
python -m edgekit.sim_pty --speed 20        # python/ 에서 실행, 출력된 /dev/pts/N 사용
./cpp/build-rel/edgekit_poll /dev/pts/N --n 5 --period-ms 500
```

## 검증 방식 (CI)

| 작업 | 무엇을 막나 |
|---|---|
| Python 단위·속성 테스트 (pytest + Hypothesis) | 프로토콜 규칙 위반, 결함 상황에서의 잘못된 값 |
| C++ GCC/Clang × AddressSanitizer + UBSan, 경고=오류 | 메모리 오류, 정의되지 않은 동작, 암묵적 형 변환 |
| clang-tidy (경고=오류) | 스레드 안전성, 검증 없는 문자열→숫자 변환, 확인 없는 optional 접근 |
| libFuzzer 2분 + 비-Clang용 대체 퍼저 | 임의 바이트에서 크래시·불변식 위반 |
| C++ ↔ Python 차등 테스트 | 이식 과정에서 생긴 동작 차이 |
| C++ CLI ↔ Python 가상 장비 (실제 시리얼 회선 규칙) | 프레임 경계·타임아웃 처리 |

## AI 코딩 도구를 쓴 방식

이 리포의 C++ 이식과 테스트는 AI 코딩 도구(Claude)로 작성했고, 방향 설정·검증 기준·리뷰는 직접 했습니다.
**AI가 틀린 곳과 그것을 무엇이 잡았는지**를 [AI_LOG.md](AI_LOG.md)에 그대로 기록했습니다.
검증 도구가 실제로 결함을 잡는지도 일부러 버그를 심어 확인했습니다.

## 저작자

조원세 (Wonse Cho) · MIT License
