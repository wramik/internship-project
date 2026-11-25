import json
import os
import random
import re
import shutil
import sys
import time
import traceback

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


PATH_TO_VIDEO_FOLDER = "./video"

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 25
ZOOM_FACTOR = 2.0
ZOOM_RAMP_SEC = 0.5


CURSOR_SCRIPT = r"""
(() => {
    const install = () => {
        if (!document.documentElement || document.getElementById("__pw_visual_layer")) {
            return;
        }

        const host = document.createElement("div");
        host.id = "__pw_visual_layer";
        host.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;overflow:visible;pointer-events:none!important;z-index:2147483647!important;";

        const root = host.attachShadow({ mode: "open" });
        root.innerHTML = `
            <style>
                .highlight {
                    position: fixed;
                    left: 0;
                    top: 0;
                    width: 64px;
                    height: 64px;
                    border-radius: 50%;
                    background: rgba(255, 220, 0, 0.35);
                    transform: translate(-50%, -50%);
                    pointer-events: none;
                    opacity: 0;
                    z-index: 1;
                }
                .cursor {
                    position: fixed;
                    left: 0;
                    top: 0;
                    width: 32px;
                    height: 32px;
                    pointer-events: none;
                    opacity: 0;
                    z-index: 2;
                }
                .ripple {
                    position: fixed;
                    width: 64px;
                    height: 64px;
                    border: 3px solid rgba(255, 0, 0, 0.95);
                    border-radius: 50%;
                    box-sizing: border-box;
                    transform: translate(-50%, -50%) scale(0.15);
                    opacity: 0.9;
                    pointer-events: none;
                    animation: pw-ripple 700ms ease-out forwards;
                    z-index: 3;
                }
                @keyframes pw-ripple {
                    from {
                        transform: translate(-50%, -50%) scale(0.15);
                        opacity: 0.9;
                    }
                    to {
                        transform: translate(-50%, -50%) scale(1);
                        opacity: 0;
                    }
                }
                .callout {
                    position: fixed;
                    top: 128px;
                    left: 50%;
                    width: fit-content;
                    max-width: 80vw;
                    padding: 12px 20px;
                    box-sizing: border-box;
                    font: 16pt Arial, sans-serif;
                    line-height: 1.25;
                    text-align: center;
                    overflow-wrap: anywhere;
                    transform: translate(-50%, -8px);
                    opacity: 0;
                    pointer-events: none;
                    transition: opacity 5s ease;
                    z-index: 5;
                }
                .callout.visible {
                    opacity: 1;
                }
            </style>
            <div class="highlight"></div>
            <svg class="cursor" viewBox="0 0 32 32" aria-hidden="true">
                <path d="M2 1 L2 27 L9 20 L14 30 L19 27 L14 18 L25 18 Z"
                      fill="#ffffff" stroke="#000000" stroke-width="1.6"
                      stroke-linejoin="round" />
            </svg>
        `;

        document.documentElement.appendChild(host);

        const highlight = root.querySelector(".highlight");
        const cursor = root.querySelector(".cursor");

        let lastPointer = { x: 0, y: 0 };
        let zoomRingTimer = null;

        const zoomRing = document.createElement("div");
        zoomRing.style.cssText = `
            position: fixed;
            width: 100px;
            height: 100px;
            border: 5px solid #00cc66;
            border-radius: 50%;
            box-sizing: border-box;
            transform: translate(-50%, -50%);
            pointer-events: none;
            display: none;
            opacity: 0;
            transition: opacity 250ms ease;
            z-index: 10;
        `;
        root.appendChild(zoomRing);

        window.__zoomAt = async (x, y) => {
            x = Number(x);
            y = Number(y);

            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return null;
            }

            x = Math.max(0, Math.min(window.innerWidth - 1, x));
            y = Math.max(0, Math.min(window.innerHeight - 1, y));

            if (typeof window.__reportZoomEvent !== "function") {
                return null;
            }

            const eventType = await window.__reportZoomEvent({ x, y });

            if (eventType !== "in" && eventType !== "out") {
                return null;
            }

            zoomRing.style.left = `${x}px`;
            zoomRing.style.top = `${y}px`;
            zoomRing.style.borderColor =
                eventType === "in" ? "#00cc66" : "#ff3333";
            zoomRing.style.display = "block";

            requestAnimationFrame(() => {
                zoomRing.style.opacity = "1";
            });

            if (zoomRingTimer !== null) {
                window.clearTimeout(zoomRingTimer);
            }

            zoomRingTimer = window.setTimeout(() => {
                zoomRing.style.opacity = "0";

                window.setTimeout(() => {
                    zoomRing.style.display = "none";
                }, 250);
            }, 900);

            return { x, y, type: eventType };
        };

        window.addEventListener("keydown", event => {
            if (event.key !== "F5") {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            if (event.repeat) {
                return;
            }

            window.__zoomAt(lastPointer.x, lastPointer.y);
        }, true);

        window.addEventListener("mousemove", event => {
            const x = event.clientX;
            const y = event.clientY;

            lastPointer = { x, y };

            highlight.style.left = `${x}px`;
            highlight.style.top = `${y}px`;
            cursor.style.left = `${x}px`;
            cursor.style.top = `${y}px`;
            highlight.style.opacity = "1";
            cursor.style.opacity = "1";
        }, true);

        window.addEventListener("click", event => {
            const ripple = document.createElement("div");
            ripple.className = "ripple";
            ripple.style.left = `${event.clientX}px`;
            ripple.style.top = `${event.clientY}px`;
            root.appendChild(ripple);
            window.setTimeout(() => ripple.remove(), 800);
        }, true);

        window.ShowCallout = (text, bgColor, textColor, fade) => {
            const oldCallout = root.querySelector(".callout");
            if (oldCallout) {
                oldCallout.remove();
            }

            if (window.__pwCalloutTimer) {
                window.clearTimeout(window.__pwCalloutTimer);
            }

            if (window.__pwCalloutRemoveTimer) {
                window.clearTimeout(window.__pwCalloutRemoveTimer);
            }

            const callout = document.createElement("div");
            callout.className = "callout";
            callout.textContent = String(text);
            callout.style.backgroundColor = bgColor || "#ffdf70";
            callout.style.color = textColor || "#111111";
            root.appendChild(callout);

            if (fade) {
                requestAnimationFrame(() => callout.classList.add("visible"));

                window.__pwCalloutTimer = window.setTimeout(() => {
                    callout.classList.remove("visible");

                    window.__pwCalloutRemoveTimer = window.setTimeout(
                        () => callout.remove(),
                        2000
                    );
                }, 2000);
            } else {
                callout.classList.add("visible");

                window.__pwCalloutTimer = window.setTimeout(
                    () => callout.remove(),
                    3000
                );
            }
        };
    };

    if (document.documentElement) {
        install();
    } else {
        document.addEventListener("DOMContentLoaded", install, { once: true });
    }
})();
"""


def _move_to_locator(page, locator):
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()

    if box is None:
        raise RuntimeError("Element is not visible")

    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2

    page.mouse.move(x, y, steps=100)

    return x, y


def _safe_filename(value):
    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        str(value)
    ).strip(" .")

    return name or "company"


def delete_failed_video(context, browser, video, recorded_path=None):

    if context is not None:
        try:
            context.close()
        except Exception as e:
            print(f"Context close error: {e}")

    if browser is not None:
        try:
            browser.close()
        except Exception as e:
            print(f"Browser close error: {e}")

    video_path = recorded_path

    if video is not None and not video_path:
        try:
            video_path = video.path()
        except Exception as e:
            print(f"Video path error: {e}")

    if video_path and os.path.isfile(video_path):
        try:
            os.remove(video_path)
            print(f"Deleted failed video: {video_path}")
        except OSError as e:
            print(f"Could not delete failed video: {e}")


def captchaWizard(page):
    captcha_selectors = [
        'body > div.anomaly-modal__mask > div',
        'body > div.anomaly-modal__mask',
        '.anomaly-modal__mask',
    ]

    for selector in captcha_selectors:
        locator = page.locator(selector)

        if locator.count() > 0:
            print(f"Captcha found! {locator}")

            page.screenshot(
                path="debug_search_page.png",
                full_page=True
            )

            return True

    return False


def CaptureVideo_Magento(
        url,
        company_name,
        language,
        country,
        postcode,
        city,
        street,
        house_no,
):
    os.makedirs(PATH_TO_VIDEO_FOLDER, exist_ok=True)

    output_path = os.path.abspath(
        os.path.join(
            PATH_TO_VIDEO_FOLDER,
            f"{_safe_filename(company_name)}.webm"
        )
    )

    browser = None
    context = None
    video = None
    recorded_path = None
    video_saved = False
    zoom_events = []

    try:
        with sync_playwright() as playwright:

            for attempt in range(2):

                headless_mode = False

                zoom_events = []
                zoom_state = {
                    "active": False
                }

                print(
                    f"Starting browser. Headless: {headless_mode}"
                )

                browser = playwright.chromium.launch(
                    headless=headless_mode,
                    channel="chromium"
                )

                context = browser.new_context(
                    viewport={
                        "width": VIDEO_WIDTH,
                        "height": VIDEO_HEIGHT
                    },
                    record_video_dir=os.path.abspath(PATH_TO_VIDEO_FOLDER),
                    record_video_size={
                        "width": VIDEO_WIDTH,
                        "height": VIDEO_HEIGHT
                    },
                )

                recording_started_at = time.monotonic()

                page = context.new_page()
                video = page.video

                def on_zoom_event(source, event):
                    if not isinstance(event, dict):
                        return None

                    try:
                        x = float(event["x"])
                        y = float(event["y"])

                        if not (
                            0 <= x < VIDEO_WIDTH
                            and 0 <= y < VIDEO_HEIGHT
                        ):
                            return None

                    except (KeyError, TypeError, ValueError):
                        return None

                    event_type = (
                        "out" if zoom_state["active"] else "in"
                    )

                    zoom_state["active"] = not zoom_state["active"]

                    zoom_events.append({
                        "t": time.monotonic() - recording_started_at,
                        "x": x,
                        "y": y,
                        "type": event_type
                    })

                    return event_type

                page.expose_binding(
                    "__reportZoomEvent",
                    on_zoom_event
                )

                page.add_init_script(CURSOR_SCRIPT)

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                page.evaluate(
                    "(text) => window.ShowCallout(text, '#ffdf70', '#111111', true)",
                    f"Opening {url}",
                )

                search_box = page.locator(
                    'textarea[name="q"]'
                ).first

                search_box.wait_for(
                    state="visible",
                    timeout=30000
                )

                page.wait_for_timeout(1500)

                search_x, search_y = _move_to_locator(
                    page,
                    search_box
                )

                page.mouse.click(search_x, search_y)

                query = f"Magento developer in {city}"

                for character in query:
                    page.keyboard.type(character)
                    page.wait_for_timeout(
                        random.randint(80, 130)
                    )

                search_button = page.locator(
                    'form button[type="submit"]:visible'
                ).first

                search_button.wait_for(
                    state="visible",
                    timeout=30000
                )

                button_x, button_y = _move_to_locator(
                    page,
                    search_button
                )

                try:
                    with page.expect_navigation(
                            wait_until="domcontentloaded",
                            timeout=20000
                    ):
                        page.wait_for_timeout(500)
                        page.mouse.click(button_x, button_y)

                except PlaywrightTimeoutError:
                    pass

                page.wait_for_timeout(3000)

                captcha_found = captchaWizard(page)

                if captcha_found and headless_mode:
                    print(
                        "CAPTCHA DETECTED! Restarting without headless..."
                    )

                    delete_failed_video(
                        context,
                        browser,
                        video
                    )

                    context = None
                    browser = None
                    video = None

                    continue

                if captcha_found and not headless_mode:
                    print(
                        "CAPTCHA detected. Solve it manually."
                    )

                    page.pause()

                result = page.locator(
                    'a[data-testid="result-title-a"]'
                ).first

                try:
                    result.wait_for(
                        state="visible",
                        timeout=30000
                    )

                except PlaywrightTimeoutError:

                    print("Search results not found.")

                    print(f"Current URL: {page.url}")
                    print(f"Page title: {page.title()}")

                    page.screenshot(
                        path="debug_search_page.png",
                        full_page=True
                    )

                    captcha_found = captchaWizard(page)

                    if headless_mode:

                        print(
                            "Headless search failed. "
                            "Restarting browser in visible mode..."
                        )

                        delete_failed_video(
                            context,
                            browser,
                            video
                        )

                        context = None
                        browser = None
                        video = None

                        continue

                    if captcha_found:

                        print(
                            "Please solve CAPTCHA manually."
                        )

                        page.pause()

                        result.wait_for(
                            state="visible",
                            timeout=60000
                        )

                    else:

                        raise Exception(
                            "Search results not found even in headed mode. "
                            f"URL: {page.url}"
                        )

                page.wait_for_timeout(1500)

                result_x, result_y = _move_to_locator(
                    page,
                    result
                )

                page.wait_for_timeout(1500)

                try:
                    with page.expect_navigation(
                            wait_until="domcontentloaded",
                            timeout=20000
                    ):
                        page.mouse.click(
                            result_x,
                            result_y
                        )

                except PlaywrightTimeoutError:
                    pass

                context.close()
                context = None

                recorded_path = video.path()

                browser.close()
                browser = None

                break

            else:

                raise Exception(
                    "Search failed after both browser attempts."
                )

        if not recorded_path or not os.path.isfile(recorded_path):
            return None

        if os.path.exists(output_path):
            os.remove(output_path)

        shutil.move(
            recorded_path,
            output_path
        )

        video_name, _ = os.path.splitext(output_path)
        zoom_json_path = f"{video_name}.zoom.json"

        if zoom_events:
            zoom_sidecar = {
                "viewport": {
                    "width": VIDEO_WIDTH,
                    "height": VIDEO_HEIGHT
                },
                "fps": VIDEO_FPS,
                "zoom_factor": ZOOM_FACTOR,
                "ramp_sec": ZOOM_RAMP_SEC,
                "events": zoom_events
            }

            with open(
                zoom_json_path,
                "w",
                encoding="utf-8"
            ) as file:
                json.dump(
                    zoom_sidecar,
                    file,
                    indent=4,
                    ensure_ascii=False
                )
        elif os.path.exists(zoom_json_path):
            os.remove(zoom_json_path)

        video_saved = True

        return os.path.abspath(output_path)

    except Exception as e:

        print(f"Error for {company_name}: {e}")
        traceback.print_exc()

        return None

    finally:

        if not video_saved:
            delete_failed_video(
                context,
                browser,
                video,
                recorded_path
            )

        else:

            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

