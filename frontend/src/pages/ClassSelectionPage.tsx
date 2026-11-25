import { ArrowRight, Plus, School } from 'lucide-react';
import { SUBJECT_LABELS } from '../api';
import type { ClassOut } from '../api/wire';
import { Button, PageHeader } from '../components';
import type { Role } from '../types';
import styles from './ClassSelectionPage.module.css';

type Props = {
  role: Role;
  classes?: readonly ClassOut[];
  error: Error | null;
  onSelect: (classId: string) => void;
  onRetry: () => void;
};

export function ClassSelectionPage({ role, classes, error, onSelect, onRetry }: Props) {
  const teacher = role === 'teacher';
  return (
    <section className={styles.selection} aria-label="클래스 선택">
      <PageHeader title="클래스 선택" actions={teacher && (
        <Button variant="secondary" disabled icon={<Plus size={18} aria-hidden="true" />}>클래스 개설</Button>
      )} />
      {error ? (
        <div className={styles.notice}>
          <p role="alert">{error.message}</p>
          <Button variant="secondary" onClick={onRetry}>다시 불러오기</Button>
        </div>
      ) : !classes ? (
        <p className={styles.notice} role="status">클래스를 불러오는 중…</p>
      ) : classes.length === 0 ? (
        <div className={styles.notice}>
          <School size={28} aria-hidden="true" />
          <p>참여 중인 클래스가 없습니다.</p>
          {!teacher && <p className={styles.meta}>클래스 참여는 담당 교사에게 문의해 주세요.</p>}
        </div>
      ) : (
        <ul className={styles.classes}>
          {classes.map((classroom) => (
            <li key={classroom.id}>
              <button type="button" className={styles.card} onClick={() => onSelect(classroom.id)}>
                <span className={styles.icon}><School size={24} aria-hidden="true" /></span>
                <span className={styles.details}>
                  <strong>{classroom.name}</strong>
                  <span className={styles.meta}>
                    {classroom.academicYear}학년도 · {classroom.gradeLabel}
                    {classroom.teacherSubject && ` · 담당 교과 ${SUBJECT_LABELS[classroom.teacherSubject]}`}
                  </span>
                </span>
                <ArrowRight className={styles.arrow} size={20} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
