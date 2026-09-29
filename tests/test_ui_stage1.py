# -*- coding: utf-8 -*-
"""Этап 1 фронта: hero, аккордеоны, поиск/фильтр твиков, карточки конфигов,
превью категорий, тосты и shimmer - всё проверяется offscreen."""
from __future__ import annotations

import typing as t


def _sys_tweaks() -> t.List[t.Dict[str, t.Any]]:
    return [
        {"id": "explorer.one", "category": "explorer",
         "name_ru": "Проводник один", "name_en": "Explorer one",
         "risk": "low", "reboot": False, "explorer_restart": False},
        {"id": "telemetry.two", "category": "telemetry",
         "name_ru": "Телеметрия два", "name_en": "Telemetry two",
         "risk": "medium", "reboot": False, "explorer_restart": False},
        {"id": "security.three", "category": "security",
         "name_ru": "Безопасность три", "name_en": "Security three",
         "risk": "high", "reboot": True, "explorer_restart": False},
    ]


class TestAccordion:
    def test_toggle_hides_body(self, qapp: t.Any) -> None:
        from ui.theme import Accordion
        acc = Accordion("Секция", open=False)
        assert not acc.is_open()
        from PySide6.QtWidgets import QLabel
        label = QLabel("внутри")
        acc.body_layout().addWidget(label)
        # Тело прячется через maximumHeight: ноль - значит свернуто.
        assert acc._body.maximumHeight() == 0
        acc.toggle()
        assert acc.is_open()
        assert acc._body.maximumHeight() > 0
        acc.set_count(7)
        assert acc._count.text() == "7"
        acc.set_open(False)
        assert not acc.is_open()


class TestTweaksTabStage1:
    def _page(self) -> t.Any:
        from ui.tabs import TweaksTab
        page = TweaksTab()
        page.set_tweaks([], [], [], sys_tweaks=_sys_tweaks())
        return page

    def test_accordions_per_category(self, qapp: t.Any) -> None:
        page = self._page()
        titles = [a._header.text() for a in page._accordions]
        assert any("Проводник" in x for x in titles)
        assert any("Телеметрия" in x for x in titles)
        assert any("Безопасность" in x for x in titles)

    def test_search_filters_rows(self, qapp: t.Any) -> None:
        from PySide6.QtTest import QTest

        page = self._page()
        page._search.setText("телеметрия")
        QTest.qWait(250)  # дождаться дебаунса поиска (180 мс)
        titles = [a._header.text() for a in page._accordions]
        assert any("Телеметрия" in x for x in titles)
        assert not any("Проводник" in x for x in titles)
        assert not any("Безопасность" in x for x in titles)

        page._search.setText("zzz-нет-такого")
        QTest.qWait(250)
        titles = [a._header.text() for a in page._accordions]
        assert not any(
            cat in title for title in titles
            for cat in ("Проводник", "Телеметрия", "Безопасность"))

    def test_risk_filter(self, qapp: t.Any) -> None:
        page = self._page()
        idx = page._risk_filter.findData("high")
        page._risk_filter.setCurrentIndex(idx)
        titles = [a._header.text() for a in page._accordions]
        assert any("Безопасность" in x for x in titles)
        assert not any("Телеметрия" in x for x in titles)

    def test_config_cards_select(self, qapp: t.Any) -> None:
        page = self._page()
        page.setConfigs([
            {"name": "A", "tweaks": 2, "apps": 1, "redists": 0,
             "created": 1, "build": 0, "note": "", "file": "a.json"},
            {"name": "B", "tweaks": 0, "apps": 0, "redists": 1,
             "created": 2, "build": 0, "note": "", "file": "b.json"},
        ])
        assert page.current_config_name() == "A"
        assert page._config_apply_btn.isEnabled()
        page._select_config("B")
        assert page.current_config_name() == "B"
        fired: t.List[str] = []
        page.configApplyRequested.connect(lambda name: fired.append(name))
        page._emit_config_apply()
        assert fired == ["B"]
        page.setConfigs([])
        assert not page._config_apply_btn.isEnabled()


class TestCleanerPreview:
    def test_chips_visible_without_cards(self, qapp: t.Any) -> None:
        from ui.tabs import CleanerTab
        page = CleanerTab()
        page.set_last_summary({"temp.app": ("Временные файлы", 1234567),
                               "installers": ("Установщики", 800)})
        from PySide6.QtWidgets import QFrame
        chips = [w for w in page.findChildren(QFrame)
                 if w.objectName() == "previewChip"]
        assert len(chips) == 2
        # Страница не показана: isVisible() требует видимых предков,
        # поэтому смотрим собственный флаг скрытости чипов.
        assert not any(c.isHidden() for c in chips)
        page.set_categories([{"id": "temp.app", "title": "Временные файлы",
                              "files": 3, "bytes": 10, "lane": "trash",
                              "risk": "low", "regrows": True,
                              "admin": False}])
        assert all(c.isHidden() for c in chips)


class TestHeroAndTiles:
    def test_hero_stats_and_nav(self, qapp: t.Any) -> None:
        from ui.tabs import AdvisorTab
        page = AdvisorTab()
        page.set_hero_stats("01.02 03:04", "1.2 ГБ", "3.4 ГБ", "73")
        hero = page.hero()
        assert hero is not None
        values = [v.text() for v in hero._stat_values.values()]
        assert values == ["01.02 03:04", "1.2 ГБ", "3.4 ГБ", "73"]
        fired: t.List[int] = []
        page.navigateRequested.connect(lambda i: fired.append(i))
        tiles = [page._hero_tiles[0][0]]
        tiles[0].click()
        assert fired == [1]

    def test_toast_host_shows(self, qapp: t.Any) -> None:
        from PySide6.QtWidgets import QFrame, QWidget
        from ui.theme import ToastHost
        host_widget = QWidget()
        host = ToastHost(host_widget)
        host.show_message("конфиг сохранён", "ok")
        toasts = [w for w in host.findChildren(QFrame)
                  if w.objectName() == "toast"]
        assert len(toasts) == 1

    def test_shimmer_paints(self, qapp: t.Any) -> None:
        from ui.theme import ShimmerProgress
        bar = ShimmerProgress()
        bar.setRange(0, 100)
        bar.setValue(40)
        bar.resize(200, 18)
        bar.show()
        bar.grab()
        bar.setValue(80)
        bar.grab()
        bar.hide()
