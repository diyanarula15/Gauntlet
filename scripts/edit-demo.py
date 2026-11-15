"""Edit browser footage into a narrated, captioned 1080p MP4."""
import json, subprocess, textwrap
from pathlib import Path
root=Path(__file__).resolve().parents[1]
scenes=json.loads((root/'docs/demo-video-script.json').read_text())
audio=root/'demo/audio'
def call(args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
def stamp(seconds):
 ms=round(seconds*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
subtitles=[]
for i,scene in enumerate(scenes):
 source=audio/f'scene-{i}.aiff';duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(source)]))
 assert duration<18.8,(i,duration)
 call(['ffmpeg','-v','error','-y','-i',str(source),'-af','loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000,asetpts=N/SR/TB,adelay=700:all=1,apad=whole_len=960000,atrim=end_sample=960000,asetpts=N/SR/TB,afade=t=out:st=19:d=1','-ar','48000','-ac','2',str(audio/f'edited-{i}.wav')])
 rendered=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(audio/f'edited-{i}.wav')]))
 assert abs(rendered-20)<.001,(i,rendered)
 subtitles.append(f'{i+1}\n{stamp(i*20+.7)} --> {stamp(i*20+.7+duration)}\n'+ '\n'.join(textwrap.wrap(scene['narration'],65))+'\n')
(root/'demo/FAULTLINE-Demo.srt').write_text('\n'.join(subtitles))
(audio/'concat.txt').write_text('\n'.join(f"file 'edited-{i}.wav'" for i in range(9)))
call(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(audio/'concat.txt'),'-c:a','pcm_s16le',str(audio/'narration.wav')])
chapters=';FFMETADATA1\n'+''.join(f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={i*20000}\nEND={(i+1)*20000}\ntitle={scene["title"]}\n' for i,scene in enumerate(scenes))
(root/'demo/chapters.txt').write_text(chapters)
record=json.loads((root/'demo/recording.json').read_text());video=root/record['video']
pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-t','5','-vf','crop=2:2:0:788,fps=25','-pix_fmt','rgb24','-f','rawvideo','-'])
frames=[pixels[j:j+12] for j in range(0,len(pixels),12)]
index=next((j for j,p in enumerate(frames) if len(p)>=3 and p[0]>100 and p[2]>170 and p[2]>p[0]+35 and p[2]>p[1]+35),None)
if index is None:raise RuntimeError('Could not locate editorial start frame')
offset=index/25
(root/'demo/edit-timing.json').write_text(json.dumps({'trimmed_startup_seconds':offset,'duration_seconds':180,'chapters':9,'narration_delay_seconds':.7},indent=2))
call(['ffmpeg','-hide_banner','-loglevel','warning','-y','-ss',str(offset),'-i',str(video),'-i',str(audio/'narration.wav'),'-i',str(root/'demo/FAULTLINE-Demo.srt'),'-f','ffmetadata','-i',str(root/'demo/chapters.txt'),'-map_metadata','3','-map_chapters','3','-map','0:v:0','-map','1:a:0','-map','2:0','-vf','scale=1920:1080:flags=lanczos,fade=t=in:st=0:d=0.45,fade=t=out:st=179:d=1','-r','30','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-c:s','mov_text','-metadata:s:s:0','language=eng','-disposition:s:0','0','-t','180','-movflags','+faststart','-metadata','title=FAULTLINE — AI Agent Crash-Test Lab','-metadata','comment=Actual local application recording. Offline model and search providers are explicitly simulated. System-generated narration.',str(root/'demo/FAULTLINE-Complete-Demo.mp4')])
print('Final MP4 exported')
