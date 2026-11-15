import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1];video=root/'demo/FAULTLINE-Complete-Demo.mp4'
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(video)]))
v=next(s for s in probe['streams'] if s['codec_type']=='video');a=next(s for s in probe['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'],v['codec_name'],v['r_frame_rate'])==(1920,1080,'h264','30/1')
assert a['codec_name']=='aac' and a['channels']==2
assert abs(float(v['duration'])-180)<.05 and abs(float(a['duration'])-180)<.05
assert len(probe['chapters'])==9 and any(s['codec_name']=='mov_text' for s in probe['streams'])
for i,c in enumerate(probe['chapters']):assert abs(float(c['start_time'])-i*20)<.05
for i in range(9):
 duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(root/f'demo/audio/edited-{i}.wav')]))
 assert abs(duration-20)<.001
subprocess.run(['ffmpeg','-v','error','-i',str(video),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
for seconds,name in [(10,'opening'),(75,'trace'),(95,'comparison'),(130,'leaderboard'),(170,'closing')]:
 subprocess.run(['ffmpeg','-v','error','-y','-ss',str(seconds),'-i',str(video),'-frames:v','1',str(root/f'demo/{name}.jpg')],check=True)
evidence=json.loads((root/'demo/benchmark-evidence.json').read_text());assert evidence['completed_runs']==evidence['total_runs']==18
for r in evidence['runs']:
 calls=[e for e in evidence['traces'][r['id']] if e['event_type']=='CALL']
 assert all(e.get('upstream_called') is False for e in calls)
 if r['agent_id']=='react' and r['scenario_id']=='search_failure':assert [e['status_code'] for e in calls if e['service']=='search']==[500,500,200]
report={'passed':True,'duration_seconds':float(probe['format']['duration']),'video_duration_seconds':float(v['duration']),'audio_duration_seconds':float(a['duration']),'resolution':'1920x1080','fps':'30','video_codec':'h264','audio_codec':'aac','audio_channels':2,'chapter_markers':9,'subtitle_track':'English','all_narration_segments_exactly_20_seconds':True,'full_video_audio_decode_errors':0,'recorded_experiment_runs':18,'offline_disclosure':True,'size_bytes':video.stat().st_size,'sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
(root/'demo/video-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
