# IntelliLearn Question Generation Logic Summary

## 1. CURRENT QUESTION GENERATION METHOD(S)

### Question Generation Flow
Questions are **AI-generated** when an educator publishes a quiz from a lecture material. The system supports two generation strategies:

1. **Primary: Gemini API** (Google Generative AI)
    - Model: `gemini-2.5-flash` (configurable via `GEMINI_MODEL`)
   - Configurable via `GEMINI_API_KEY` environment variable
   - Supports retry logic: `GEMINI_PRIMARY_ATTEMPTS` (default: 2) with `GEMINI_PRIMARY_RETRY_DELAY` (default: 0.8s)

2. **Fallback: Local T5-based Model**
   - Model: `google/flan-t5-base` (configurable via `LOCAL_FALLBACK_MODEL`)
   - Runs if Gemini fails/times out
   - Supports retry logic: `LOCAL_FALLBACK_ATTEMPTS` (default: 1) with `LOCAL_FALLBACK_RETRY_DELAY` (default: 0.5s)

### Generation Trigger Point
**Location**: `content_app/views.py` → `publish_quiz()` function (line 385-470)

- Called when educator clicks "Publish Quiz" on a verified summary
- Requires: **Summary must be verified (IsVerified=True) and not archived (IsArchived=False)**
- Extracts raw text from uploaded lecture material file
- Generates questions based on selected publish mode (MCQ, Constructed, or Both)

---

## 2. DATA SOURCES & DATA FLOW

### Input Data Sources
| Source | Access Method | Format |
|--------|--------------|--------|
| **Uploaded Lecture Material** | `LectureMaterial.FileData` (BinaryField) | PDF, DOCX, PPTX, TXT |
| **Raw Text Extraction** | `extract_text_from_bytes(filename, file_bytes)` | Plain text (first 12,000 chars) |
| **Concept Name** | `LectureMaterial.Title` | String |
| **Summary Object** | `Summary` model (OneToOne with LectureMaterial) | For verification check only |

### Data Flow Diagram
```
LectureMaterial (uploaded file)
    ↓
extract_text_from_bytes() [content_app/text_extraction.py]
    ↓
Raw text (max 12,000 chars)
    ↓
generate_constructed_questions() / generate_mcq_questions()
    ├─→ Gemini API (Primary)
    └─→ Local T5 Fallback (if Gemini fails)
    ↓
Generated Questions
    ↓
Stored in Question model
    ↓
Linked to:
  • Lecture (FK)
  • Concept (FK) - auto-created if needed
  • IsPublished = True
  • IsAIGenerated = True
```

---

## 3. QUESTION MODEL STRUCTURE & FIELDS

### Learning App Question Model
**File**: `learning_app/models.py` (lines 27-50)

```python
class Question(models.Model):
    # Primary Key
    QuestionID = models.AutoField(primary_key=True)
    
    # Relationships
    Lecture = models.ForeignKey(
        'content_app.LectureMaterial', 
        on_delete=models.CASCADE, 
        related_name='questions'
    )
    Concept = models.ForeignKey(
        Concept, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='questions'
    )
    
    # Content Fields
    QuestionText = models.TextField()
    QuestionType = models.CharField(
        max_length=16, 
        choices=[('mcq', 'MCQ'), ('constructed', 'Constructed-response')],
        default='constructed'
    )
    DifficultyLevel = models.CharField(max_length=50)  # 'Easy', 'Medium', 'Hard'
    CorrectAnswerText = models.TextField()
    
    # Status Flags
    IsPublished = models.BooleanField(default=False)
    IsAIGenerated = models.BooleanField(default=True)
```

### Field Descriptions
| Field | Type | Purpose |
|-------|------|---------|
| `QuestionText` | TextField | The actual question prompt/stem |
| `QuestionType` | Char(16) | **mcq** or **constructed** |
| `DifficultyLevel` | Char(50) | Easy / Medium / Hard (used in adaptive questioning) |
| `CorrectAnswerText` | TextField | Answer key / model answer |
| `IsPublished` | Boolean | Only published questions appear in quizzes |
| `IsAIGenerated` | Boolean | True for AI-generated; False for manually created by educator |
| `Lecture` (FK) | Foreign Key | Links question to source material |
| `Concept` (FK) | Foreign Key | Links question to concept (for micro-lessons & caching) |

---

## 4. AI-POWERED QUESTION GENERATION FUNCTIONS

### Orchestrator Layer (ai_services/ai_orchestrator.py)
These functions implement Gemini-first with local fallback:

#### A. General Question Generation
```python
def generate_questions(text: str) -> List[str]
```
- Generates 5-10 general study questions
- Returns list of question strings
- Currently unused in publish_quiz flow (see constructed_questions instead)

#### B. Constructed-Response Questions
```python
def generate_constructed_questions(text: str, count: int = 6) -> List[str]
```
- Generates open-ended questions requiring explanation
- **USED IN**: `publish_quiz()` for constructed question type
- Returns list of 6 question strings
- Fallback model: `generate_local_constructed_questions()`

#### C. Multiple-Choice Questions
```python
def generate_mcq_questions(text: str, count: int = 6) -> List[Dict[str, str]]
```
- Generates 4-option multiple-choice questions
- **USED IN**: `publish_quiz()` for MCQ question type
- Returns list of dicts: `{'question_text': str, 'correct_answer': 'A|B|C|D'}`
- Fallback model: `generate_local_mcq_questions()`

#### D. Retry Question Generation
```python
def generate_similar_question(question_text: str, concept_name: str = '') -> str
```
- Generates variant of original question for student retry
- **USED IN**: `learning_app/views.py` after incorrect answer
- Returns single question string
- Fallback: `generate_local_retry_question()`

#### E. Micro-Lesson Generation
```python
def generate_micro_lesson(
    question_text: str,
    student_answer: str,
    correct_answer: str,
    concept_name: str = '',
    fallback_text: str = ''
) -> str
```
- Generates brief tutoring explanation after wrong answer
- **USED IN**: `learning_app/views.py` → `submit_answer()` → `_get_or_generate_micro_lesson()`
- Stores result in `Concept.micro_lesson` for caching
- Fallback: `generate_local_micro_lesson()`

### Model Layer (ai_services/ai_models.py)
**Gemini API Functions** (lines 814-920):
- `generate_gemini_questions()` - Creates question list from text
- `generate_gemini_constructed_questions()` - Creates open-ended questions
- `generate_gemini_mcq_questions()` - Creates MCQ with A/B/C/D format
- `generate_gemini_retry_question()` - Variant of original question
- `generate_gemini_micro_lesson()` - Post-answer tutoring

**Local T5 Fallback Functions** (lines 993-1049):
- `generate_local_questions()`
- `generate_local_constructed_questions()`
- `generate_local_mcq_questions()`
- `generate_local_retry_question()`
- `generate_local_micro_lesson()`

---

## 5. QUESTION RELATIONSHIPS TO MATERIALS & SUMMARIES

### Entity Relationship Structure
```
LectureMaterial (uploaded file)
    ├─→ has one: Summary (OneToOneField)
    │       ├─ IsVerified (boolean) → GATE FOR QUESTION GENERATION
    │       ├─ IsArchived (boolean)
    │       └─ SummaryText (TextField)
    │
    └─→ has many: Question (ForeignKey)
            ├─ Lecture_id (FK to LectureMaterial)
            ├─ Concept_id (FK to Concept)
            ├─ IsPublished (boolean)
            ├─ IsAIGenerated (boolean)
            └─ relates to responses & attempts (inverse)

Question
    ├─→ Lecture (FK) - source material
    ├─→ Concept (FK) - category/topic
    └─→ QuestionResponse (reverse FK)
            └─ Attempt (FK to QuizAttempt)
```

### Question Creation in publish_quiz()
**File**: `content_app/views.py` (lines 385-470)

```python
# 1. VERIFICATION GATE
if not lecture.summary.IsVerified or lecture.summary.IsArchived:
    messages.error('Verify the summary before publishing quiz questions.')
    return redirect()

# 2. GET OR CREATE CONCEPT (from lecture title)
concept, _ = Concept.objects.get_or_create(
    ConceptName=lecture.Title,
    defaults={'Description': f'Auto-generated concept from {lecture.Title}'}
)

# 3. EXTRACT TEXT FROM MATERIAL
raw_text = extract_text_from_bytes(
    lecture.OriginalFileName, 
    lecture.FileData
)

# 4. GENERATE QUESTIONS (if not already generated)
if Question.TYPE_CONSTRUCTED not in existing_types:
    generated_constructed = generate_constructed_questions(raw_text, count=6)
    for question_text in generated_constructed:
        Question.objects.create(
            Lecture=lecture,
            Concept=concept,
            QuestionText=question_text,
            QuestionType='constructed',
            CorrectAnswerText='To be validated by educator',  # Educator validates
            DifficultyLevel='Medium',
            IsPublished=False,
            IsAIGenerated=True
        )

# 5. MCQ GENERATION (similar pattern)
if Question.TYPE_MCQ not in existing_types:
    generated_mcq = generate_mcq_questions(raw_text, count=4)
    for mcq in generated_mcq:
        Question.objects.create(
            QuestionText=mcq['question_text'],  # Full MCQ with A/B/C/D
            CorrectAnswerText=mcq['correct_answer'],  # 'A', 'B', 'C', or 'D'
            # ... other fields
        )

# 6. PUBLISH SELECTED QUESTIONS
Question.objects.filter(QuestionID__in=selected_ids).update(IsPublished=True)
```

### Three Summary Modes & Their Relationship
**File**: `ai_services/ai_orchestrator.py` (lines 99-117)

The three summary modes are: **brief**, **standard**, **detailed**
- These modes affect summary generation but **NOT question generation**
- Questions are generated independently of summary mode
- All questions are generated from the same lecture material text

### Concept-Question Link for Micro-Lessons
**File**: `learning_app/models.py` (line 21)

```python
class Concept(models.Model):
    micro_lesson = models.TextField(blank=True)
```

- Micro-lessons are **cached per Concept** (not per Question)
- First time a concept is encountered with wrong answer:
  1. Generate micro-lesson via `generate_micro_lesson()`
  2. Store in `concept.micro_lesson`
  3. Subsequent encounters reuse cached lesson

**File**: `learning_app/views.py` (lines 48-72)

---

## 6. MANUAL VS AI-GENERATED QUESTIONS

### Manual Question Creation
**File**: `content_app/views.py` → `manage_lecture_questions()` (lines 467-515)

- Educators can manually create questions via a form
- `IsAIGenerated = False` when manually created
- Stored with same model structure as AI-generated questions
- Can be edited after creation

### AI-Generated vs Manual Workflow
| Aspect | AI-Generated | Manual |
|--------|-------------|--------|
| **Trigger** | Publish quiz button | Educator input form |
| **Quantity** | 6 constructed + 4 MCQ | 1 per submission |
| **IsAIGenerated** | True | False |
| **Initial State** | IsPublished=False | IsPublished=False |
| **Answer** | Auto-generated (needs validation) | Educator-provided |
| **Flow** | Material → AI → DB → Publish | Form input → DB → Publish |

---

## 7. QUESTION GENERATION LIFECYCLE

### Step-by-Step Process
```
1. UPLOAD PHASE
   └─ Educator uploads lecture material (PDF/DOCX/PPTX)
      └─ Stored in LectureMaterial.FileData

2. SUMMARY PHASE
   └─ Extract text from material
   └─ Generate summary (Gemini or local T5)
   └─ Educator verifies summary
      └─ Sets Summary.IsVerified = True

3. QUESTION GENERATION PHASE (publish_quiz view)
   └─ Educator clicks "Publish Quiz"
   └─ System checks: Summary.IsVerified AND NOT IsArchived
      └─ IF FAIL: show error, return
   └─ Create/Get Concept from lecture title
   └─ Extract raw text (first 12,000 chars)
   └─ Generate Constructed Questions (6 questions)
      └─ Call generate_constructed_questions(raw_text, count=6)
      └─ Save to DB with IsPublished=False, IsAIGenerated=True
   └─ Generate MCQ Questions (4 questions)
      └─ Call generate_mcq_questions(raw_text, count=4)
      └─ Save to DB with IsPublished=False, IsAIGenerated=True
   └─ Publish selected questions (IsPublished=True)

4. QUIZ TAKING PHASE
   └─ Student takes quiz
   └─ Questions fetched from Question.objects.filter(IsPublished=True)
   └─ Student submits answer
   └─ System checks answer correctness
      └─ MCQ: Compare option letter or text
      └─ Constructed: Exact match with CorrectAnswerText
   └─ If incorrect:
      └─ Generate similar question for retry
      └─ Generate micro-lesson (cache in Concept)
   └─ Adaptive difficulty: Easy → Medium → Hard

5. EDUCATOR REVIEW PHASE (optional)
   └─ Educators can view/edit questions in question_manager.html
   └─ Can change difficulty, text, or answers before/after publishing
```

---

## 8. QUESTION TYPES & GENERATION FORMATS

### Constructed-Response Format
```
Generated by: generate_constructed_questions()
Example output:
"What is the relationship between photosynthesis and cellular respiration?"
"Explain how osmosis affects plant cell turgor pressure."

Stored as:
- QuestionText: entire prompt above
- QuestionType: 'constructed'
- CorrectAnswerText: 'To be validated by educator' (initial)
```

### Multiple-Choice Format
```
Generated by: generate_mcq_questions()
Gemini prompt requests format:
Q: <question>
A) <option A>
B) <option B>
C) <option C>
D) <option D>
ANSWER: <A|B|C|D>

Stored as:
- QuestionText: full question with options (A/B/C/D)
- QuestionType: 'mcq'
- CorrectAnswerText: 'A' or 'B' or 'C' or 'D'
```

### Text Extraction Limits
- First 12,000 characters of lecture material
- Handles: PDF, DOCX, PPTX, TXT
- Extraction function: `extract_text_from_bytes(filename, file_bytes)`

---

## 9. ADAPTIVE DIFFICULTY SYSTEM

**File**: `learning_app/views.py` (lines 16-19)

```python
DIFFICULTY_ORDER = ['Easy', 'Medium', 'Hard']

def _next_difficulty(current_level, is_correct):
    if is_correct:
        return DIFFICULTY_ORDER[min(idx + 1, 2)]  # Move up
    return DIFFICULTY_ORDER[max(idx - 1, 0)]      # Move down
```

- All generated questions start at **Medium** difficulty
- Student performance determines difficulty progression
- Next question selected via:
  ```sql
  Question.objects.filter(
      Concept_id=concept_id,
      DifficultyLevel=current_difficulty,
      IsPublished=True,
      Lecture__UploadedBy_id=selected_educator_id
  ).exclude(QuestionID__in=answered_ids).first()
  ```

---

## 10. CONFIGURATION & ENVIRONMENT VARIABLES

### Gemini Configuration
- `GEMINI_API_KEY`: Required to enable Gemini (if missing, uses local fallback)
- `GEMINI_MODEL`: Model name (default: `gemini-2.5-flash`)
- `GEMINI_PRIMARY_ATTEMPTS`: Retry count (default: 2)
- `GEMINI_PRIMARY_RETRY_DELAY`: Seconds between retries (default: 0.8)

### Local Fallback Configuration
- `LOCAL_FALLBACK_MODEL`: T5 model name (default: `google/flan-t5-base`)
- `LOCAL_FALLBACK_ATTEMPTS`: Retry count (default: 1)
- `LOCAL_FALLBACK_RETRY_DELAY`: Seconds between retries (default: 0.5)

### Django Integration
- Question generation calls are made directly from `content_app.views.publish_quiz()`
- Orchestrator layer (`ai_orchestrator.py`) handles retry logic and fallback
- All AI calls log to both logger and stdout with emoji prefixes:
  - 🚀 Started
  - ✅ Success
  - ⚠️ Fallback triggered
  - ℹ️ Info

---

## SUMMARY TABLE

| Aspect | Details |
|--------|---------|
| **Primary Generation Method** | Gemini API (with local T5 fallback) |
| **Question Types** | MCQ (4 options) + Constructed-response |
| **Data Source** | LectureMaterial.FileData (first 12k chars) |
| **Trigger** | Educator publishes quiz after summary verification |
| **Questions Per Type** | 6 constructed + 4 MCQ |
| **AI Generation Flag** | `IsAIGenerated = True` |
| **Initial Publish State** | `IsPublished = False` (educator must publish) |
| **Answer Validation** | MCQ: option letter; Constructed: manual educator input |
| **Micro-Lesson Caching** | Per Concept (shared across questions) |
| **Adaptive System** | Easy → Medium → Hard based on performance |
| **Summary Relationship** | Requires verified, non-archived summary |
| **Concept Auto-Creation** | Yes, from LectureMaterial.Title |
