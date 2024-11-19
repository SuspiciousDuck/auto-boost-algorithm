#Originally by Trix
#Contributors: R1chterScale, Yiss and Kosaka

from math import ceil
import json
import os
import subprocess
import re
import argparse
import psutil
import shutil
import platform
import vapoursynth as vs
core = vs.core
core.max_cache_size = 1024

IS_WINDOWS = platform.system() == 'Windows'
NULL_DEVICE = 'NUL' if IS_WINDOWS else '/dev/null'

if shutil.which("av1an") is None:
    raise FileNotFoundError("av1an not found, exiting")

if shutil.which("turbo-metrics") is None:
    print("turbo-metrics not found, defaulting to vs-zip")
    ssimu2zig = True
    default_skip = 3
else:
    ssimu2zig = False
    default_skip = 0

parser = argparse.ArgumentParser()
parser.add_argument("-s", "--stage", help = "Select stage: 1 = encode, 2 = calculate metrics, 3 = generate zones | Default: all", default=0)
parser.add_argument("-i", "--input", required=True, help = "Video input filepath (original source file)")
parser.add_argument("-q", "--quality", help = "Base quality (CRF) | Default: 30", default=30)
parser.add_argument("-d", "--deviation", help = "Maximum CRF change from original | Default: 10", default=10)
parser.add_argument("-p", "--preset", help = "Fast encode preset | Default: 9", default=9)
parser.add_argument("-w", "--workers", help = "Number of av1an workers | Default: amount of physical cores", default=psutil.cpu_count(logical=False))
parser.add_argument("-m", "--metrics", help = "Select metrics: 1 = SSIMU2, 2 = XPSNR, 3 = Both | Default: 1", default=1)
parser.add_argument("-S", "--skip", help = "SSIMU2 skip value, every nth frame's SSIMU2 is calculated | Default: 0 for turbo-metrics, 3 for vs-zip")
parser.add_argument("-z", "--zones", help = "Zones calculation method: 1 = SSIMU2, 2 = XPSNR, 3 = Multiplication, 4 = Lowest Result | Default: 1", default=1)
parser.add_argument("-a", "--aggressive", action='store_true', help = "More aggressive boosting | Default: not active")
args = parser.parse_args()
stage = int(args.stage)
src_file = args.input
output_dir = os.path.dirname(src_file)
tmp_dir = os.path.join(output_dir, "temp")
output_file = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_fastpass.mkv")
scenes_file = os.path.join(tmp_dir, "scenes.json")
br = float(args.deviation)
skip = args.skip if args.skip is not None else default_skip
aggressive = args.aggressive

def get_ranges(scenes: str) -> list[int]:
    """
    Reads a scene file and returns a list of frame numbers for each scene change.

    :param scenes: path to scene file
    :type scenes: str

    :return: list of frame numbers
    :rtype: list[int]
    """
    ranges = [0]
    with open(scenes, "r") as file:
        content = json.load(file)
        for scene in content['scenes']:
            ranges.append(scene['end_frame'])
    return ranges

def fast_pass(
        input_file: str, output_file: str, tmp_dir: str, preset: int, crf: float, workers: int
):
    """
    Quick fast-pass using Av1an

    :param input_file: path to input file
    :type input_file: str
    :param output_file: path to output file
    :type output_file: str
    :param tmp_dir: path to temporary directory
    :type tmp_dir: str
    :param preset: encoder preset
    :type preset: int
    :param crf: target CRF
    :type crf: float
    :param workers: number of workers
    :type workers: int
    """

    # Enclose paths in quotes if they contain spaces
    input_file = f'"{input_file}"' if ' ' in input_file else input_file
    output_file = f'"{output_file}"' if ' ' in output_file else output_file
    tmp_dir = f'"{tmp_dir}"' if ' ' in tmp_dir else tmp_dir

    fast_av1an_command = [
        'av1an',
        '-i', input_file,
        '--temp', tmp_dir,
        '-y',
        '--verbose',
        '--keep',
        '-m', 'lsmash',
        '-c', 'mkvmerge',
        '--min-scene-len', '24',
        '--sc-downscale-height', '720',
        '--set-thread-affinity', '2',
        '-e', 'svt-av1',
        '--force',
        '-v', f'"--preset {preset} --crf {crf:.2f} --lp 2 --scm 0 --keyint 0 --fast-decode 1 --color-primaries 1 --transfer-characteristics 1 --matrix-coefficients 1"',
        '-w', str(workers),
        '-o', output_file
    ]

    process = subprocess.run(' '.join(fast_av1an_command), shell=True, check=True)
    
    if process.returncode != 0:
        print(f"Av1an exited with code: {process.returncode}")
        exit(1)

def turbo_metrics(
    source: str, distorted: str, every: int
) -> subprocess.CompletedProcess:
    """
    Compare two files with SSIMULACRA2 using turbo-metrics.

    :param source: path to source file
    :type source: str
    :param distorted: path to distorted file
    :type distorted: str
    :param every: compare every X frames
    :type every: int

    :return: completed process
    :rtype: subprocess.CompletedProcess
    """

    turbo_cmd = [
        "turbo-metrics",
        "-m",
        "ssimulacra2",
        "--output",
        "csv",
    ]

    if every > 1:
        turbo_cmd.append("--every")
        turbo_cmd.append(str(every))

    turbo_cmd.append(source)
    turbo_cmd.append(distorted)

    return subprocess.run(
        turbo_cmd,
        capture_output=True,
        text=True,
    )

def calculate_ssimu2(src_file, enc_file, ssimu2_txt_path, ranges, skip):
    if not ssimu2zig:  # Try turbo-metrics first if ssimu2zig is False
        turbo_metrics_run = turbo_metrics(src_file, enc_file, skip)
        if turbo_metrics_run.returncode == 0:  # If turbo-metrics succeeds
            with open(ssimu2_txt_path, "w") as file:
                file.write(f"skip: {skip}\n")
            frame = 0
            # for whatever reason, turbo-metrics in csv mode dumps the entire scores to stdout at the end even though it prints them live to stdout.
            # so we need to see if we've seen ``ssimulacra2`` before and if we have, ignore anything after the second one.
            ignore_end_barf = False
            for line in turbo_metrics_run.stdout.splitlines():
                # set ignore_end_barf to true as this is the first "ssimulacra2" line
                if line == "ssimulacra2" and not ignore_end_barf:
                    ignore_end_barf = True
                # break the loop as we've encountered the second "ssimulacra2" line so we don't get a dupe of the scores.
                elif line == "ssimulacra2" and ignore_end_barf:
                    break
                # assume everything not "ssimulacra2" is a score.
                if line != "ssimulacra2":
                    frame += 1
                    with open(ssimu2_txt_path, "a") as file:
                        file.write(f"{frame}: {float(line)}\n")
            return  # Exit if turbo-metrics succeeded
        else:
            print(f"Turbo Metrics exited with code: {turbo_metrics_run.returncode}")
            print(turbo_metrics_run.stdout)
            print(turbo_metrics_run.stderr)
            print("Falling back to vs-zip")
            skip = args.skip if args.skip is not None else '3'

    # If ssimu2zig is True or turbo-metrics failed, use vs-zip
    source_clip = core.lsmas.LWLibavSource(source=src_file, cache=0)
    encoded_clip = core.lsmas.LWLibavSource(source=enc_file, cache=0)

    #source_clip = source_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s='709').fmtc.transfer(transs="srgb", transd="linear", bits=32)
    #encoded_clip = encoded_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s='709').fmtc.transfer(transs="srgb", transd="linear", bits=32)

    print(f"source: {len(source_clip)} frames")
    print(f"encode: {len(encoded_clip)} frames")
    with open(ssimu2_txt_path, "w") as file:
        file.write(f"skip: {skip}\n")
    iter = 0
    for i in range(len(ranges) - 1):
        cut_source_clip = source_clip[ranges[i]:ranges[i+1]].std.SelectEvery(cycle=skip, offsets=1)
        cut_encoded_clip = encoded_clip[ranges[i]:ranges[i+1]].std.SelectEvery(cycle=skip, offsets=1)
        result = core.vszip.Metrics(cut_source_clip, cut_encoded_clip, mode=0)
        for index, frame in enumerate(result.frames()):
            iter += 1
            score = frame.props['_SSIMULACRA2']
            with open(ssimu2_txt_path, "a") as file:
                file.write(f"{iter}: {score}\n")

def calculate_xpsnr(src_file, enc_path, xpsnr_txt_path):
    if IS_WINDOWS:
        xpsnr_txt_path = xpsnr_txt_path.replace(':', r'\\:')

    if IS_WINDOWS:
        xpsnr_command = f'ffmpeg -i "{src_file}" -i "{enc_path}" -lavfi xpsnr="stats_file={xpsnr_txt_path}" -f null {NULL_DEVICE}'
    else:
        xpsnr_command = f'ffmpeg -i {src_file} -i {enc_path} -lavfi xpsnr="stats_file={xpsnr_txt_path}" -f null {NULL_DEVICE}'
    
    p = subprocess.Popen(xpsnr_command, shell=True)
    exit_code = p.wait()

    if exit_code != 0:
        print("XPSNR encountered an error, exiting.")
        exit(-2)

def get_xpsnr(xpsnr_txt_path):
    count=0

    sum_weighted = 0
    values_weighted: list[int] = []

    with open(xpsnr_txt_path, "r") as file:
        for line in file:
            match = re.search(r"XPSNR Y: ([0-9]+\.[0-9]+)  XPSNR U: ([0-9]+\.[0-9]+)  XPSNR : ([0-9]+\.[0-9]+)", line)
            if match:
                Y = float(match.group(1))
                U = float(match.group(2))
                V = float(match.group(3))
                W = (4 * Y + U + V) / 6

                sum_weighted += W
                values_weighted.append(W)

                count += 1
            else:
                print(line)
        avg_weighted = sum_weighted / count
        for i in range(len(values_weighted)):
            values_weighted[i] /= avg_weighted
    return values_weighted

def get_ssimu2(ssimu2_txt_path):
    ssimu2_scores: list[int] = []

    with open(ssimu2_txt_path, "r") as file:
        skipmatch = re.search(r"skip: ([0-9]+)", file.readline())
        if skipmatch:
            skip = int(skipmatch.group(1))
        else:
            print("Skip value not detected in SSIMU2 file, exiting.")
            exit(-2)
        for line in file:
            match = re.search(r"([0-9]+): ([0-9]+\.[0-9]+)", line)
            if match:
                score = float(match.group(2))
                ssimu2_scores.append(score)
            else:
                print(line)
    return ssimu2_scores, skip

def calculate_std_dev(score_list: list[int]):
    """
    Takes a list of metrics scores and returns the associated arithmetic mean,
    5th percentile and 95th percentile scores.

    :param score_list: list of SSIMU2 scores
    :type score_list: list
    """

    filtered_score_list = [score for score in score_list if score >= 0]
    sorted_score_list = sorted(filtered_score_list)
    average = sum(filtered_score_list)/len(filtered_score_list)
    percentile_5 = sorted_score_list[len(filtered_score_list)//20]
    percentile_95 = sorted_score_list[int (len(filtered_score_list)//(20/19))]
    return (average, percentile_5, percentile_95)

def generate_zones(ranges: list, percentile_5_total: list, average: int, crf: float, zones_txt_path: str):
    """
    Appends a scene change to the ``zones_txt_path`` file in Av1an zones format.

    creates ``zones_txt_path`` if it does not exist. If it does exist, the line is
    appended to the end of the file.

    :param ranges: Scene changes list
    :type ranges: list
    :param percentile_5_total: List containing all 5th percentile scores
    :type percentile_5_total: list
    :param average: Full clip average score
    :type average: int
    :param crf: CRF setting to use for the zone
    :type crf: int
    :param zones_txt_path: Path to the zones.txt file
    :type zones_txt_path: str
    """
    zones_iter = 0
    for i in range(len(ranges)-1):
        zones_iter += 1
        if aggressive:
            new_crf = crf - ceil((1.0 - (percentile_5_total[i] / average)) * 40 * 4) / 4
        else:
            new_crf = crf - ceil((1.0 - (percentile_5_total[i] / average)) * 20 * 4) / 4

        if new_crf < crf - br: # set lowest allowed crf
            new_crf = crf - br

        if new_crf > crf + br: # set highest allowed crf
            new_crf = crf + br

        print(f'Enc:  [{ranges[i]}:{ranges[i+1]}]\n'
              f'Chunk 5th percentile: {percentile_5_total[i]}\n'
              f'Adjusted CRF: {new_crf:.2f}\n')

        with open(zones_txt_path, "w" if zones_iter == 1 else "a") as file:
            file.write(f"{ranges[i]} {ranges[i+1]} svt-av1 --crf {new_crf:.2f}\n")

def calculate_metrics(src_file, output_file, tmp_dir, ranges, skip, metrics):
    match metrics:
        case 1:
            ssimu2_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_ssimu2.log")
            calculate_ssimu2(src_file, output_file, ssimu2_txt_path, ranges, skip)
        case 2:
            xpsnr_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_xpsnr.log")
            calculate_xpsnr(src_file, output_file, xpsnr_txt_path)
        case 3:
            xpsnr_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_xpsnr.log")
            ssimu2_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_ssimu2.log")
            calculate_xpsnr(src_file, output_file, xpsnr_txt_path)
            calculate_ssimu2(src_file, output_file, ssimu2_txt_path, ranges, skip)

def calculate_zones(tmp_dir, ranges, zones, cq):
    match zones:
        case 1:
            ssimu2_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_ssimu2.log")
            (ssimu2_scores, skip) = get_ssimu2(ssimu2_txt_path)
            ssimu2_zones_txt_path = f"{tmp_dir}/ssimu2_zones.txt"
            ssimu2_total_scores: list[int] = []
            ssimu2_percentile_5_total = []
            ssimu2_iter = 0

            for i in range(len(ranges)-1):
                ssimu2_chunk_scores: list[int] = []
                xpsnr_chunk_scores: list[int] = []
                ssimu2_frames = (ranges[i+1] - ranges[i]) // skip
                for frames in range(ssimu2_frames):
                    ssimu2_score = ssimu2_scores[ssimu2_iter]
                    ssimu2_chunk_scores.append(ssimu2_score)
                    ssimu2_total_scores.append(ssimu2_score)
                    ssimu2_iter += 1
                (ssimu2_average, ssimu2_percentile_5, ssimu2_percentile_95) = calculate_std_dev(ssimu2_chunk_scores)
                ssimu2_percentile_5_total.append(ssimu2_percentile_5)
                #print(f'5th Percentile:  {ssimu2_percentile_5}')
            (ssimu2_average, ssimu2_percentile_5, ssimu2_percentile_95) = calculate_std_dev(ssimu2_total_scores)

            print(f'SSIMU2:')
            print(f'Median score:  {ssimu2_average}')
            print(f'5th Percentile:  {ssimu2_percentile_5}')
            print(f'95th Percentile:  {ssimu2_percentile_95}\n')
            generate_zones(ranges, ssimu2_percentile_5_total, ssimu2_average, cq, ssimu2_zones_txt_path)

        case 2:
            xpsnr_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_xpsnr.log")
            xpsnr_scores: list[int] = get_xpsnr(xpsnr_txt_path)
            xpsnr_zones_txt_path = f"{tmp_dir}/xpsnr_zones.txt"
            xpsnr_total_scores: list[int] = []
            xpsnr_percentile_5_total = []
            xpsnr_iter = 0

            for i in range(len(ranges)-1):
                xpsnr_chunk_scores: list[int] = []
                xpsnr_frames = (ranges[i+1] - ranges[i])
                for frames in range(xpsnr_frames):
                    xpsnr_score = xpsnr_scores[xpsnr_iter]
                    xpsnr_chunk_scores.append(xpsnr_score)
                    xpsnr_total_scores.append(xpsnr_score)
                    xpsnr_iter += 1
                (xpsnr_average, xpsnr_percentile_5, xpsnr_percentile_95) = calculate_std_dev(xpsnr_chunk_scores)
                xpsnr_percentile_5_total.append(xpsnr_percentile_5)
            (xpsnr_average, xpsnr_percentile_5, xpsnr_percentile_95) = calculate_std_dev(xpsnr_total_scores)

            print(f'XPSNR:')
            print(f'Median score:  {xpsnr_average}')
            print(f'5th Percentile:  {xpsnr_percentile_5}')
            print(f'95th Percentile:  {xpsnr_percentile_95}\n')
            generate_zones(ranges, xpsnr_percentile_5_total, xpsnr_average, cq, xpsnr_zones_txt_path)

        case 3:
            ssimu2_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_ssimu2.log")
            (ssimu2_scores, skip) = get_ssimu2(ssimu2_txt_path)
            xpsnr_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_xpsnr.log")
            xpsnr_scores: list[int] = get_xpsnr(xpsnr_txt_path)

            multiplied_zones_txt_path = f"{tmp_dir}/multiplied_zones.txt"
            multiplied_total_scores: list[int] = []
            multiplied_percentile_5_total = []
            multiplied_iter = 0
            for i in range(len(ranges)-1):
                multiplied_chunk_scores: list[int] = []
                ssimu2_frames = (ranges[i+1] - ranges[i]) // skip
                for frames in range(ssimu2_frames):
                    ssimu2_score = ssimu2_scores[multiplied_iter]
                    xpsnr_index = (skip*frames) + ranges[i] + 1
                    xpsnr_scores_averaged = 0
                    for avg_index in range(skip):
                        xpsnr_scores_averaged += xpsnr_scores[xpsnr_index + avg_index - 1]
                    xpsnr_scores_averaged /= skip
                    multiplied_score = xpsnr_scores_averaged * ssimu2_score
                    multiplied_chunk_scores.append(multiplied_score)
                    multiplied_total_scores.append(multiplied_score)
                    multiplied_iter += 1
                (multiplied_average, multiplied_percentile_5, multiplied_percentile_95) = calculate_std_dev(multiplied_chunk_scores)
                multiplied_percentile_5_total.append(multiplied_percentile_5)
            (multiplied_average, multiplied_percentile_5, multiplied_percentile_95) = calculate_std_dev(multiplied_total_scores)

            print(f'Multiplied:')
            print(f'Median score:  {multiplied_average}')
            print(f'5th Percentile:  {multiplied_percentile_5}')
            print(f'95th Percentile:  {multiplied_percentile_95}\n')
            generate_zones(ranges, multiplied_percentile_5_total, multiplied_average, cq, multiplied_zones_txt_path)


        case 4:
            ssimu2_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_ssimu2.log")
            (ssimu2_scores, skip) = get_ssimu2(ssimu2_txt_path)
            xpsnr_txt_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(src_file))[0]}_xpsnr.log")
            xpsnr_scores: list[int] = get_xpsnr(xpsnr_txt_path)

            minimum_zones_txt_path = f"{tmp_dir}/minimum_zones.txt"
            minimum_total_scores: list[int] = []
            minimum_percentile_5_total = []
            minimum_iter = 0
            ssimu2_total_scores: list[int] = []
            for ssimu2_iter in range(len(ssimu2_scores)-1):
                ssimu2_total_scores.append(ssimu2_scores[ssimu2_iter])
            (ssimu2_average, ssimu2_percentile_5, ssimu2_percentile_95) = calculate_std_dev(ssimu2_total_scores)

            for i in range(len(ranges)-1):
                minimum_chunk_scores: list[int] = []
                ssimu2_frames = (ranges[i+1] - ranges[i]) // skip
                for frames in range(ssimu2_frames):
                    ssimu2_score = ssimu2_scores[minimum_iter]
                    xpsnr_index = (skip*frames) + ranges[i] + 1
                    xpsnr_scores_averaged = 0
                    for avg_index in range(skip):
                        xpsnr_scores_averaged += xpsnr_scores[xpsnr_index + avg_index - 1]
                    xpsnr_scores_averaged /= skip
                    xpsnr_scores_averaged *= ssimu2_average
                    minimum_score = min(ssimu2_score, xpsnr_scores_averaged)
                    minimum_chunk_scores.append(minimum_score)
                    minimum_total_scores.append(minimum_score)
                    minimum_iter += 1
                (minimum_average, minimum_percentile_5, minimum_percentile_95) = calculate_std_dev(minimum_chunk_scores)
                minimum_percentile_5_total.append(minimum_percentile_5)
            (minimum_average, minimum_percentile_5, minimum_percentile_95) = calculate_std_dev(minimum_total_scores)

            print(f'Minimum:')
            print(f'Median score:  {minimum_average}')
            print(f'5th Percentile:  {minimum_percentile_5}')
            print(f'95th Percentile:  {minimum_percentile_95}\n')
            generate_zones(ranges, minimum_percentile_5_total, minimum_average, cq, minimum_zones_txt_path)

match stage:
    case 0:
        workers = args.workers
        crf = float(args.quality)
        preset = args.preset
        fast_pass(src_file, output_file, tmp_dir, preset, crf, workers)
        ranges = get_ranges(scenes_file)
        metrics = int(args.metrics)
        calculate_metrics(src_file, output_file, tmp_dir, ranges, skip, metrics)
        zones = int(args.zones)
        calculate_zones(tmp_dir, ranges, zones, crf)
    case 1:
        workers = args.workers
        crf = float(args.quality)
        preset = args.preset
        fast_pass(src_file, output_file, tmp_dir, preset, crf, workers)
    case 2:
        ranges = get_ranges(scenes_file)
        metrics = int(args.metrics)
        calculate_metrics(src_file, output_file, tmp_dir, ranges, skip, metrics)
    case 3:
        ranges = get_ranges(scenes_file)
        zones = int(args.zones)
        crf = float(args.quality)
        calculate_zones(tmp_dir, ranges, zones, crf)
    case _:
        print(f"Stage argument invalid, exiting.")
        exit(-2)