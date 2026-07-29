import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
// 演示机内存紧：默认单并发，降低本机 Chromium OOM 概率
Config.setConcurrency(1);
