from test_controller import controller

def prepare(controller):
    controller._preferences.screenshot_enabled=True
    controller._preferences.screenshot_interval_ms=2000
    controller._screenshot_capture_settings={'screenshots':True,'screenshot_interval_ms':2000}
    controller._samples={'fps':[{'x':0,'y':60},{'x':40,'y':60}]}
    controller._screenshots=[{'seconds':2,'time':'00:02','image':'a'},{'seconds':4,'time':'00:04','image':'b'}]

def test_locked_gap_has_no_selected_old_screenshot_and_explains_why(controller):
    prepare(controller)
    controller.setTimeLockRatio(.1)
    assert controller.selectedScreenshotIndex==1
    controller.setTimeLockRatio(.8)
    assert controller.selectedScreenshotIndex==-1
    assert controller.screenshotStatusText=='该时段暂无截图'

def test_turning_off_has_immediate_notice_and_preserves_prior_evidence(controller):
    prepare(controller)
    controller._collecting=True
    generation=controller._screenshot_target_generation
    controller.setScreenshotEnabled(False)
    assert '截图已关闭' in controller.screenshotStatusText
    assert not controller._screenshot_timer.isActive()
    assert controller._screenshot_target_generation>generation
    assert len(controller.screenshots)==2 and controller.selectedScreenshotIndex==-1
    changes=controller._screenshot_settings_changes
    assert len(changes)==1 and not changes[0]['settings']['screenshots']
    controller._collecting=False
    controller.setTimeLockRatio(.1)
    assert controller.selectedScreenshotIndex==1

def test_record_view_uses_recorded_settings_changes_not_current_preference(controller):
    prepare(controller)
    from perfzy.domain.models import Platform
    controller._viewing_platform=Platform.WINDOWS
    controller._viewed_metadata={'duration':40,'capture_settings':{'screenshots':True,'screenshot_interval_ms':2000},
                                'capture_settings_changes':[{'elapsed':4.1,'settings':{'screenshots':False}}]}
    controller.setTimeLockRatio(.11)
    assert controller.selectedScreenshotIndex==-1
    assert controller.screenshotStatusText=='该时段截图已关闭'

def test_reenable_does_not_reuse_previous_period_and_valid_new_image_can_match(controller):
    prepare(controller)
    controller._screenshot_settings_changes=[{'elapsed':5,'settings':{'screenshots':False}},
                                            {'elapsed':20,'settings':{'screenshots':True}}]
    controller.setTimeLockRatio(.51)
    assert controller.selectedScreenshotIndex==-1
    controller._screenshots.append({'seconds':22,'time':'00:22','image':'new'})
    controller._sync_screenshot_selection()
    assert controller.selectedScreenshotIndex==2 and not controller.screenshotStatusText

def test_late_callback_from_disabled_generation_cannot_add_image(controller,tmp_path):
    prepare(controller)
    controller._collecting=True
    controller._active_collection_token=9
    controller._screenshot_inflight_token=9
    old_generation=controller._screenshot_target_generation
    controller.setScreenshotEnabled(False)
    controller._accept_screenshot({'token':9,'generation':old_generation,'elapsed':4,'path':''})
    assert len(controller.screenshots)==2 and not controller._screenshot_working
    assert not controller._screenshot_timer.isActive()

def test_same_switch_value_does_not_cancel_current_capture_or_add_history(controller):
    prepare(controller)
    controller._collecting=True
    generation=controller._screenshot_target_generation
    controller.setScreenshotEnabled(True)
    assert controller._screenshot_target_generation==generation
    assert not controller._screenshot_settings_changes

def test_paused_collection_does_not_claim_performance_sampling_is_continuing(controller):
    prepare(controller)
    controller._collecting=True
    controller._paused=True
    controller.setScreenshotEnabled(False)
    assert controller.screenshotStatusText=='截图已关闭 · 采集已暂停'

def test_disabled_period_survives_save_and_reopen(controller,tmp_path):
    import json
    from perfzy.storage.sessions import LiveSessionWriter,save_session
    prepare(controller)
    sid='shot-availability'
    directory=tmp_path/'data/sessions'/sid
    directory.mkdir(parents=True)
    image=directory/'saved.jpg'
    image.write_bytes(b'test screenshot file')
    shots=[{'seconds':4,'time':'00:04','image':image.as_uri()}]
    spool=LiveSessionWriter(tmp_path,sid,manifest={'capture_settings':controller._screenshot_capture_settings})
    controller._session_spool=spool
    controller._collecting=True
    controller.setScreenshotEnabled(False)
    spool.close()
    controller._session_spool=None
    controller._collecting=False
    path=save_session(tmp_path,{'id':'windows:local'},{'id':'windows:1','name':'game'},
                      controller._samples,shots,{'fps'},sid,duration=80)
    payload=json.loads(path.read_text(encoding='utf-8'))
    assert payload['capture_settings']['screenshots'] is True
    assert payload['capture_settings_changes'][0]['settings']['screenshots'] is False
    controller.openSession(sid)
    controller.setTimeLockRatio(.6)
    assert controller.selectedScreenshotIndex==-1
    assert controller.screenshotStatusText=='该时段截图已关闭'
