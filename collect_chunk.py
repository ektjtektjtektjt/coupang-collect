"""
collect_chunk.py
=================
GitHub Actions 환경에서 실행되는 수집 스크립트.
chunk.json 을 읽어 판매량/판매자를 수집하고 result.json 으로 저장.
"""

import asyncio
import re
import random
import json
from playwright.async_api import async_playwright

# ========== 설정 ==========
동시실행수 = 2
연속실패_최대 = 5
휴식_간격 = 50        # N개마다 휴식
휴식_시간 = 120       # 초
# ==========================

결과 = []
연속실패 = 0
차단됨 = False
lock = asyncio.Lock()


def 링크만들기(상품ID, 아이템ID):
    return f"https://www.coupang.com/vp/products/{상품ID}?itemId={아이템ID}"


def 판매량텍스트(숫자):
    if 숫자 >= 10000:
        return f"{숫자 // 10000}만명 이상"
    elif 숫자 >= 1000:
        return f"{숫자:,}명 이상"
    elif 숫자 > 0:
        return f"{숫자}명 이상"
    return "정보없음"


def 리뷰대비판매량(판매수, 리뷰수):
    if 리뷰수 == 0:
        return "리뷰없음"
    return round(판매수 / 리뷰수, 1)


async def 판매량수집(page, 링크):
    global 연속실패, 차단됨
    try:
        response = await page.goto(링크, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(3, 6))
        html = await page.content()

        if response and response.status == 403:
            print(f"  🚨 403 차단 감지")
            차단됨 = True
            return None, None

        if "Access Denied" in html or "access denied" in html.lower():
            print(f"  🚨 Access Denied 감지")
            차단됨 = True
            return None, None

        if response and response.status != 200:
            print(f"  ⚠️  상태코드: {response.status}")
            연속실패 += 1
            return None, None

        연속실패 = 0

        # 판매량 파싱
        판매수 = 0
        if "구매했어요" in html:
            idx = html.find("구매했어요")
            구간 = html[idx - 200:idx]
            if "만명" in 구간:
                m = re.search(r"(\d+)만명", 구간)
                if m:
                    판매수 = int(m.group(1)) * 10000
            elif "천명" in 구간:
                m = re.search(r"(\d+)천명", 구간)
                if m:
                    판매수 = int(m.group(1)) * 1000
            else:
                m = re.search(r"([\d,]+)명", 구간)
                if m:
                    판매수 = int(m.group(1).replace(",", ""))

        # 판매자 파싱
        판매자 = "없음"
        m = re.search(r'sellerName\\":\\"([^"\\]+)\\"', html)
        if not m:
            m = re.search(r'"sellerName":"([^"]+)"', html)
        if m:
            판매자 = m.group(1).strip()

        return 판매수, 판매자

    except Exception as e:
        print(f"  오류: {e}")
        연속실패 += 1
        return None, None


async def 상품처리(semaphore, i, 상품, 전체수):
    global 연속실패, 차단됨, 결과

    if 차단됨 or 연속실패 >= 연속실패_최대:
        return

    링크 = 링크만들기(상품["상품ID"], 상품["아이템ID"])
    print(f"[{i+1}/{전체수}] {상품['카테고리']} 랭킹{상품['순위']}등 | {상품['상품명'][:25]}...")

    async with semaphore:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 400, "height": 600},
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 9; SM-S9260) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Mobile Safari/537.36"
                ),
            )
            page = await context.new_page()
            판매수, 판매자 = await 판매량수집(page, 링크)
            await browser.close()

    if 차단됨:
        print("  🚨 차단으로 중단")
        return

    if 연속실패 >= 연속실패_최대:
        print(f"  ⛔ 연속 {연속실패_최대}회 실패, 중단")
        return

    if 판매수 is None:
        print("  ❌ 실패")
        return

    상품["판매수"]        = 판매수
    상품["판매량텍스트"]   = 판매량텍스트(판매수)
    상품["판매자"]         = 판매자
    상품["리뷰대비판매량"] = 리뷰대비판매량(판매수, 상품["리뷰수"])

    print(f"  ✅ {판매량텍스트(판매수)} | {판매자}")

    async with lock:
        결과.append(상품)

    # N개마다 휴식
    if (i + 1) % 휴식_간격 == 0:
        print(f"\n⏸️  {i+1}개 완료 — {휴식_시간}초 휴식 중...\n")
        await asyncio.sleep(휴식_시간)
    else:
        await asyncio.sleep(random.uniform(4, 8))


async def 메인():
    with open("chunk.json", "r", encoding="utf-8") as f:
        상품목록 = json.load(f)

    print(f"수집 시작: {len(상품목록)}개 (동시 {동시실행수}개)\n")

    semaphore = asyncio.Semaphore(동시실행수)
    tasks = [
        상품처리(semaphore, i, 상품, len(상품목록))
        for i, 상품 in enumerate(상품목록)
    ]
    await asyncio.gather(*tasks)

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(결과, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(결과)}/{len(상품목록)}개 → result.json 저장")


if __name__ == "__main__":
    asyncio.run(메인())
