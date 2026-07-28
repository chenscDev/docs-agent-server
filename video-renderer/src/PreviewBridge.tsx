/**
 * 可选：后续用 @remotion/player 替换 HTML 预览时，保持同一消息协议。
 *
 * 协议（与 app/video/preview_page.py / RN RemotionPreview 一致）：
 * - RN → Web: preview/update { props, frame? } | preview/seek { frame }
 * - Web → RN: preview/ready | preview/frame { frame }
 *
 * 接入步骤简述：
 * 1. yarn add @remotion/player
 * 2. 用 Player + TalkingCaptions，ref.seekTo(frame)；inputProps=props
 * 3. window.__RN_PREVIEW__.handle = 同一套 type 分支
 */
export {};
