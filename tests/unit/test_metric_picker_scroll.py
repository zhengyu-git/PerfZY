"""Keep the metric currently being edited in view across catalog rebuilds."""
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_qml_chart_input import capture_page, _wheel


def click(window, item):
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                    item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint())
    QTest.qWait(70)


def open_picker(window, find):
    click(window, find('metricPickerButton'))
    return find('metricChartList'), find('metricSelectionPanel')


def test_metric_selection_and_reopening_keep_scroll_position(capture_page):
    window, controller, find = capture_page
    chart_list, panel = open_picker(window, find)
    panel.setProperty('showAdvanced', True)
    panel.setProperty('showSystem', True)
    QTest.qWait(60)
    for _ in range(3):
        _wheel(window, chart_list.mapToScene(QPointF(60, chart_list.height() / 2)))
    QTest.qWait(350)
    before = chart_list.property('contentY')
    assert before > 20

    # Changing a checkbox or a group switch replaces the QVariantList model.
    controller.setMetricEnabled('frame_time', False)
    QTest.qWait(70)
    assert chart_list.property('contentY') == pytest.approx(before, abs=2)
    click(window, find('closeMetricPickerButton'))
    chart_list, panel = open_picker(window, find)
    assert chart_list.property('contentY') == pytest.approx(before, abs=2)


def test_expansion_and_checkbox_preserve_visible_group(capture_page):
    window, controller, find = capture_page
    chart_list, panel = open_picker(window, find)
    panel.setProperty('showAdvanced', True)
    panel.setProperty('showSystem', True)
    QTest.qWait(60)
    # CPU is several rows down. Its expansion must not send the list back up.
    row = find('metricGroup:cpu')
    chart_list.setProperty('contentY', row.y() - 10)
    QTest.qWait(50)
    point = row.mapToScene(QPointF(50, 25))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
    QTest.qWait(70)
    assert panel.property('expandedChart') == 'cpu'
    row = find('metricGroup:cpu')
    assert row.y() - chart_list.property('contentY') == pytest.approx(10, abs=2)

    metric = find('metricCheck:app_cpu')
    click(window, metric)
    row = find('metricGroup:cpu')
    assert row.y() - chart_list.property('contentY') == pytest.approx(10, abs=2)
    assert panel.property('expandedChart') == 'cpu'
    before = chart_list.property('contentY')
    for _ in range(3):
        _wheel(window, row.mapToScene(QPointF(50, 25)))
    QTest.qWait(350)
    assert chart_list.property('contentY') > before + 1
