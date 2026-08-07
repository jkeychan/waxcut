import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'No Decode Step',
    description: (
      <>
        MP3 frames are self-describing, so their boundaries are found
        directly from the byte stream — no ffmpeg, no subprocess, no
        decode/re-encode round trip.
      </>
    ),
  },
  {
    title: 'Byte-Identical Output',
    description: (
      <>
        Cuts are made by byte-copying whole frames from the source file.
        Output is byte-identical to the source audio frames, just shorter —
        tags and VBR header metadata aren't carried into split output.
      </>
    ),
  },
  {
    title: 'Continuously Verified',
    description: (
      <>
        Duration parsing is cross-validated against{' '}
        <a href="https://github.com/quodlibet/mutagen">mutagen</a>&apos;s
        independent implementation to the millisecond, and fuzzed
        continuously with ClusterFuzzLite.
      </>
    ),
  },
];

function Feature({title, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center padding-horiz--md">
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
