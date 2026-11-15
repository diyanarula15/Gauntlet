"""Replace trace/comparison chapters with focused detail pickups; preserve synchronized audio."""
import subprocess,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def offset(video):
 raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-t','15','-vf','crop=2:2:0:788,fps=25','-pix_fmt','rgb24','-f','rawvideo','-'])
 return next(i/25 for i,j in enumerate(range(0,len(raw),12)) if len(raw[j:j+3])==3 and raw[j]>100 and raw[j+2]>170 and raw[j+2]>raw[j]+35 and raw[j+2]>raw[j+1]+35)
record=json.loads((root/'demo/recording.json').read_text());timing=json.loads((root/'demo/edit-timing.json').read_text());pickups=json.loads((root/'demo/pickups.json').read_text())
clips=[Path(p['video']) for p in pickups];starts=[offset(p) for p in clips]
filters='[0:v]split=2[s0][s1];[s0]trim=end=60,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,fps=30[a];[s1]trim=start=100:end=180,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,fps=30[d];[1:v]trim=duration=20,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,fps=30[b];[2:v]trim=duration=20,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,fps=30[c];[a][b][c][d]concat=n=4:v=1:a=0,fade=t=in:st=0:d=0.45,fade=t=out:st=179:d=1[v]'
args=['ffmpeg','-hide_banner','-loglevel','warning','-y','-ss',str(timing['trimmed_startup_seconds']),'-i',record['video']]
for start,clip in zip(starts,clips):args+=['-ss',str(start),'-i',str(clip)]
args+=['-i',str(root/'demo/audio/narration.wav'),'-i',str(root/'demo/FAULTLINE-Demo.srt'),'-f','ffmetadata','-i',str(root/'demo/chapters.txt'),'-filter_complex',filters,'-map','[v]','-map','3:a:0','-map','4:0','-map_metadata','5','-map_chapters','5','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-c:s','mov_text','-metadata:s:s:0','language=eng','-disposition:s:0','0','-t','180','-movflags','+faststart','-metadata','title=FAULTLINE — Complete Product Demo','-metadata','comment=Actual local app, offline simulated providers. System-generated English narration. Focused trace/comparison detail shots.',str(root/'demo/FAULTLINE-Complete-Demo.mp4')]
subprocess.run(args,check=True)
(root/'demo/pickup-timing.json').write_text(json.dumps({'replaced_chapters':[4,5],'pickup_offsets_seconds':starts},indent=2))
print('Polished final video exported')
