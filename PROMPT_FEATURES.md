# Prompt / Image-to-Prompt

VietAssist now uses one shared prompt engine for `/prompt` and image-to-prompt.

## Modes

### 1. Described subject

Normal `/prompt` or an image without a face-lock keyword.

The generated prompt is self-contained: it describes the subject in words and does not
refer to an unavailable reference image.

### 2. Locked character

Triggered by phrases such as `cô gái 20` or `gái 20`.

The prompt starts with a fixed identity block for the recurring 20-year-old Vietnamese
character. Scene descriptions are not allowed to override the locked face.

### 3. Reference identity

Triggered by phrases such as `giữ mặt`, `mặt tôi`, `giữ khuôn mặt`.

The prompt explicitly relies on the attached reference image and avoids inventing facial
features that could conflict with that reference. The user should attach the reference
image again when pasting the generated prompt into an image model.

## Image analysis

When an image is sent through Telegram or Zalo, Gemini Vision receives a structured
instruction that requires:

- orientation and aspect ratio
- shot size
- camera height/angle
- approximate camera distance
- subject scale and headroom
- geometrically precise body pose
- clothing and accessories
- environment and composition
- lighting and colour
- focal length, aperture and depth of field
- photographic finish matched to the actual reference

The neutral example embedded in the instruction is explicitly marked as structure-only so
the model should not copy its scene, outfit, pose, lens or lighting.

## Security / reliability

- Telegram images are limited to 10 MB.
- Unsupported Telegram document image extensions are rejected/normalized.
- Google Vision validates file size, extension and common image magic bytes before upload.
- Temporary files are always removed.
- Zalo image failures caused by invalid/blocked URLs return a friendly error instead of
  becoming an unhandled bridge failure.
- User-provided captions are delimited as untrusted visual requests and are not allowed
  to rewrite the prompt-engineering rules.

## Commands

```text
/prompt <mô tả>
```

Or simply send an image to Telegram/Zalo. An image caption can select a mode or request a
visual change, for example:

```text
giữ mặt tôi, đổi sang áo dài
cô gái 20 đứng ở quán cà phê
chụp lại đúng bố cục, ánh sáng và camera
```
